from uuid import uuid4

import pytest

from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext
from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt
from crewos_ingestion.lifecycle.registration.submit_source import SubmitSource
from crewos_ingestion.submission_checks.checks import SourceSubmissionDenied, SubmissionChecks


class RecordingSourceStore:
    """Record calls without writing to a database."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, SourceSubmission, str | None]] = []
        self.receipt = SourceAcceptanceReceipt(source_id=uuid4(), version=1)

    def register(
        self,
        *,
        tenant_id: str,
        submission: SourceSubmission,
        idempotency_key: str | None = None,
    ) -> SourceAcceptanceReceipt:
        self.calls.append((tenant_id, submission, idempotency_key))
        return self.receipt


def test_authorized_submission_reaches_storage_with_verified_tenant():
    store = RecordingSourceStore()
    use_case = SubmitSource(checks=SubmissionChecks(), store=store)
    submission = SourceSubmission(title="Webhook Recovery", content="Check delivery logs.")
    context = CallerContext(tenant_id="tenant_a", permissions={"source.create"})

    receipt = use_case.execute(submission=submission, context=context, idempotency_key="upload-42")

    assert store.calls == [("tenant_a", submission, "upload-42")]
    assert receipt == store.receipt


def test_denied_submission_never_reaches_storage_even_with_a_key():
    store = RecordingSourceStore()
    use_case = SubmitSource(checks=SubmissionChecks(), store=store)
    submission = SourceSubmission(title="Webhook Recovery", content="Check delivery logs.")
    context = CallerContext(tenant_id="tenant_a", permissions={"source.read"})

    with pytest.raises(SourceSubmissionDenied):
        use_case.execute(submission=submission, context=context, idempotency_key="upload-42")

    assert store.calls == []
