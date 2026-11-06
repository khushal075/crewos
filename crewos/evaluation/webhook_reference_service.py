"""Real loopback service implementing the documented integration wire contract."""

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from time import sleep
from urllib.parse import unquote


@contextmanager
def webhook_server():
    calls = []
    tokens = {"tenant_a": "test-token-a", "tenant_b": "test-token-b"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            calls.append(
                {
                    "method": "GET",
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                }
            )
            segments = self.path.split("/")
            if (
                len(segments) != 6
                or segments[1:3] != ["v1", "tenants"]
                or segments[4] != "deliveries"
            ):
                self.respond(404, {"error": "not_found"})
                return
            tenant, delivery = unquote(segments[3]), unquote(segments[5])
            if self.headers.get("Authorization") != "Bearer " + tokens.get(tenant, "absent"):
                self.respond(403, {"private_error": "must-never-be-logged"})
                return
            if delivery == "missing" or (tenant == "tenant_b" and delivery == "only_a"):
                self.respond(404, {"error": "not_found"})
                return
            if delivery == "slow":
                sleep(0.2)
            if delivery in {"error401", "error403", "error429", "error500", "error503"}:
                self.respond(int(delivery[-3:]), {"private_error": "must-never-be-logged"})
                return
            if delivery == "redirect":
                self.respond(302, {}, headers={"Location": "/capture-token"})
                return
            if delivery == "oversize":
                self.respond(200, {"private_payload": "x" * 2000})
                return
            data = {
                "tenant_id": tenant,
                "delivery_id": delivery,
                "state": "delivered",
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
            if delivery == "wrong_tenant":
                data["tenant_id"] = "tenant_b"
            elif delivery == "wrong_id":
                data["delivery_id"] = "some_other_delivery"
            elif delivery == "unknown":
                data["state"] = "new_provider_state"
            elif delivery == "bad_state":
                data["state"] = 200
            elif delivery == "retry":
                data["state"] = "retrying"
            if delivery == "bad_json":
                self.respond(200, b"private malformed response", raw=True)
            elif delivery == "duplicate_keys":
                self.respond(200, b'{"tenant_id":"tenant_a","tenant_id":"tenant_b"}', raw=True)
            elif delivery == "mime":
                self.respond(200, data, content_type="text/html")
            elif delivery == "empty204":
                self.respond(204, b"", raw=True)
            elif delivery == "gzip":
                self.respond(200, data, headers={"Content-Encoding": "gzip"})
            else:
                self.respond(200, data)

        def do_POST(self):
            calls.append({"method": "POST", "path": self.path})
            self.respond(405, {"error": "read_only"})

        def respond(
            self, status, data, *, raw=False, content_type="application/json", headers=None
        ):
            body = data if raw else json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)
