"""Disposable scripted OpenAI-compatible endpoint for Compose verification."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if request["model"] == "fixture-primary":
            status = 503
            payload = {"error": {"message": "Synthetic primary unavailable"}}
        else:
            history = json.loads(request["messages"][-1]["content"])["messages"]
            if any(message["role"] == "tool" for message in history):
                decision = {"type": "final_answer", "answer": "The incident note awaits approval."}
            else:
                decision = {
                    "type": "tool_call",
                    "name": "add_demo_incident_note",
                    "arguments": {
                        "incident_id": "smoke-incident",
                        "note": "Synthetic verification",
                    },
                }
            status = 200
            payload = {
                "id": "fixture",
                "object": "chat.completion",
                "created": 1,
                "model": request["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": json.dumps(decision)},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40},
            }
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    with ThreadingHTTPServer(("0.0.0.0", 8080), Handler) as server:
        server.serve_forever()
