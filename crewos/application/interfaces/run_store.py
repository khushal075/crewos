"""Persistent run boundary. Implementations must fence stale execution attempts."""

from typing import Protocol


class RunNotFound(LookupError):
    pass


class RunConflict(RuntimeError):
    pass


class RunLeaseLost(RunConflict):
    pass


class RunCancelled(RuntimeError):
    pass


class UnsafeReplay(RunConflict):
    pass


class RunStoreInterface(Protocol):
    def create(
        self, job: dict, *, duration_seconds: float, idempotency_key: str | None = None
    ) -> dict: ...
    def get(self, run_id: str, tenant_id: str) -> dict: ...
    def mark_published(self, run_id: str, tenant_id: str) -> None: ...
    def claim(self, job: dict) -> dict: ...
    def guard(self, run_id: str, tenant_id: str, token: str) -> float: ...
    def checkpoint(self, run_id: str, tenant_id: str, token: str, checkpoint: dict) -> None: ...
    def finish(
        self, run_id: str, tenant_id: str, token: str, *, state: str, result=None, error=None
    ) -> None: ...
    def cancel(self, run_id: str, tenant_id: str) -> dict: ...
    def resume(self, run_id: str, tenant_id: str) -> dict: ...
