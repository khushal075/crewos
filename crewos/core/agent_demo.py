"""Local agent demonstration using existing tenant grants and model settings."""

import argparse
from getpass import getpass
import os
import sys

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from crewos.application.dtos.decision_request import DecisionMessage
from crewos.core.agent_setup import build_agent_loop
from crewos.core.tenant import get_tenant_id, get_tool_execution_context
from crewos.infrastructure.task_ownership import get_task_store
from crewos.infrastructure.agent_metrics import agent_metrics, write_metrics_snapshot
from crewos.infrastructure.task_status_reader import CeleryTaskStatusReader
from crewos.third_party.llm.ollama_adapter import OllamaLLMProvider


def nonblank(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("must not be blank")
    return value.strip()


def positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a positive integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the CrewOS task-status agent demo")
    parser.add_argument("--tenant-id", required=True, type=nonblank)
    parser.add_argument("--message", required=True, type=nonblank)
    parser.add_argument("--max-decisions", type=positive_integer, default=5)
    parser.add_argument(
        "--metrics-file",
        type=nonblank,
        help="Save process-local Prometheus metrics after an attempted agent run",
    )
    args = parser.parse_args(argv)
    run_attempted = False

    try:
        token = os.environ.get("CREWOS_TENANT_TOKEN") or getpass("Tenant token: ")
        tenant_id = get_tenant_id(
            x_tenant_id=args.tenant_id,
            credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
        )
        context = get_tool_execution_context(tenant_id=tenant_id)
        model = OllamaLLMProvider().get()
        reader = CeleryTaskStatusReader(get_task_store())
        loop = build_agent_loop(model=model, reader=reader, max_decisions=args.max_decisions)
        run_attempted = True
        answer = loop.run(
            messages=[DecisionMessage(role="user", content=args.message)],
            context=context,
        )
    except HTTPException:
        print("Tenant authentication failed.", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Agent run failed ({type(error).__name__}).", file=sys.stderr)
        return 1
    finally:
        if args.metrics_file and run_attempted:
            try:
                write_metrics_snapshot(args.metrics_file, agent_metrics)
            except Exception as error:
                print(f"Metrics snapshot failed ({type(error).__name__}).", file=sys.stderr)

    print(answer.answer)
    for source in answer.citations:
        print(
            f"Source: {source.document_id} version={source.document_version} chunk={source.chunk_index}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
