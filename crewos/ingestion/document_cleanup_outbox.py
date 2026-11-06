"""Durable vector-cleanup delivery boundary; adapters must enforce tenant and lease ownership."""

from typing import Protocol
from uuid import UUID

from crewos.ingestion.document_cleanup_contracts import (
    CleanupFailureCode,
    DocumentCleanupClaim,
)


class CleanupLeaseLost(RuntimeError):
    """Missing, expired, replaced or wrongly scoped cleanup ownership."""


class CleanupOutboxCorruption(RuntimeError):
    """Persisted event or delivery identity violates its validated contract."""


class DocumentCleanupOutboxInterface(Protocol):
    """Internal worker boundary; never derive tenant scope from public event input.

    The source store must create DocumentDeletedEvent in the same transaction as
    source deletion. There is deliberately no independent append method here: two
    separate commits cannot guarantee event delivery after a crash. Source delete
    integration is not implemented by merely defining this interface.

    Initial delivery has one destination: the vector cache. Additional consumers
    need independent delivery records; they must not share one completion flag.
    No global scan is exposed. Workers receive trusted tenant scope and adapters
    must apply it before selecting, reclaiming, mutating or decoding events.
    """

    def claim_next(self, *, tenant_id: str, lease_seconds: float) -> DocumentCleanupClaim | None:
        """Atomically lease one eligible pending/retry event or reclaim an expired lease.

        Validate finite 0 < lease_seconds <= 300 and tenant identity. Choose by
        occurrence time then event ID; skip unexpired leases and future retries.
        Increment attempt and generate a new lease token on every claim. Return
        None only when no event is eligible. Revalidate event/row identity and
        propagate corruption/infrastructure errors instead of skipping or completing.
        """
        ...

    def complete(self, *, tenant_id: str, event_id: UUID, lease_token: UUID) -> None:
        """Mark delivered only after the idempotent target cleanup succeeds.

        Require the current unexpired tenant-scoped lease. A repeated completion
        with the same final token is a no-op; missing/other-tenant/replaced/expired
        active claims raise CleanupLeaseLost. Old workers cannot complete a newly
        claimed event. Completing an outbox event is not deleting the source.
        """
        ...

    def retry(
        self,
        *,
        tenant_id: str,
        event_id: UUID,
        lease_token: UUID,
        delay_seconds: float,
        error_code: CleanupFailureCode,
    ) -> None:
        """Release a current lease and schedule a bounded retry, never lose the event.

        Validate finite 0 <= delay_seconds <= 86400 and a known failure code.
        Require the current unexpired tenant-scoped token, otherwise raise
        CleanupLeaseLost. Store only the code, never source content or raw errors.
        A worker crash is recovered by lease expiry, not this method. Backoff and
        alert thresholds belong to the worker; no automatic terminal discard.
        """
        ...
