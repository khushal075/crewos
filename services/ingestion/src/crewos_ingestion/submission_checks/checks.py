"""
Authorization check performed before registering a source.

"""
from crewos_ingestion.contracts.context_contracts.caller_context import CallerContext

SOURCE_CREATE_PERMISSION = "source.create"

class SourceSubmissionDenied(PermissionError):
    """
    Caller doesn't have the permission to register a source.
    """

class SubmissionChecks:
    """
    Check whether a caller may submit a source.
    """

    def check(self, context: CallerContext):
        if SOURCE_CREATE_PERMISSION not in context.permissions:
            raise SourceSubmissionDenied(
                "caller lacks permission to register a source"
            )

