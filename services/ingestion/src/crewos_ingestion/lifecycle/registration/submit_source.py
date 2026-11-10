"""
Register a source after checking thw caller's permission.
"""

from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt
from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext

from crewos_ingestion.storage.source_store import SourceStore
from crewos_ingestion.submission_checks.checks import SubmissionChecks

class SubmitSource:
    """
    Coordinate authorization and storage for source submission.
    """
    def __init__(self, *, checks: SubmissionChecks, store: SourceStore) -> None:
        self._checks = checks
        self._store = store

    def execute(
            self,
            *,
            submission: SourceSubmission,
            context: CallerContext,
            idempotency_key: str | None = None,
    ):
        self._checks.check(context)
        return self._store.register(
            tenant_id=context.tenant_id,
            submission=submission,
            idempotency_key=idempotency_key
        )