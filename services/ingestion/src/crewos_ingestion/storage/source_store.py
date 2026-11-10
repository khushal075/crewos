"""
Storage interface for registering a source and handling submission retries.
"""

from typing import Protocol

from crewos_ingestion.contracts.context_contracts.caller_context import TenantId
from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission
from crewos_ingestion.contracts.output_contracts.source_registration import SourceAcceptanceReceipt

class SourceRegistrationConflict(RuntimeError):
    """
    The tenant already used this retry key for a different submission.
    """

class SourceStore(Protocol):
    """
    The storage operations ingestion needs for source registrations.
    """

    def register(
            self,
            *,
            tenant_id: TenantId,
            submission: SourceSubmission,
            idempotency_key: str | None = None,

    ) -> SourceAcceptanceReceipt:
        """
        Register a source at version 1 or return previously saved receipt.

        An idempotency key identifies one intended submission.
        The caller reuse it across HTTP attempts for that submission.
        Keys are scoped to tenant, so another tenant using the same key
        does not affect this operation.

        So if
            1. same key,  same tenant -> Retry
            2. different key, different tenant -> First time Operation
            3. different key, same tenant ->  Different operation for the first time
            4. same key, different tenant ->  Different operation for the second time

        Case 1 - No key supplied:
            Create a new source at version 1.
            caution:
                Repeating a call without a key can create another source, even
                submitted data is identical.

        Case 2 - First attempt with a new key.
            The tenant has no commited registration for this key.
            Create a new source at version 1.
            Save the keys association with the submission and receipt in the same transaction.

        Case 3 - Retry after an attemp that did not commit.
            The caller reuses the key, but there is still no commited registration for it.
            So (IT Follows Case 2) storage doesn't need to know whether this is the caller's
            first HTTP attempt or a retry.

        Case 4 - The same key was used by another tenant.
            Another tenant registration doesn't count.
            If the current tenant has no commited registration for this key, follows CASE 2.

        Case 5 - Identical retry after a successful commit.
            The tanant and key matched to a commited registration and the validated submission data matches.
            Rerun the orignal receipt without craeting another source or version.
            This also covers a lost http response after a successful commit.

        Case 6 - Same key, different submission.
            The tenant and key mateched a commited registration,
            but the validated title, content or source reference differs.
            SourceRegistrationConflict raises an exception without creating source.

        Case 7 - Concurrent attemps with the same tenant and key
            At most one attemp can create a source.
            Once one commits, Any other attempt can complete the lookup.
             - in case of data matches: Follow Case 5
             - in case of data differs: Follow Case 6

         Return an acceptance receipt only when storage confirms a commited registration.
         The source and its key associated must commit together.
        """

        ...
