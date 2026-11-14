"""
Stored source revision and versioning rules.
"""

from typing import Self
from pydantic import BaseModel, UUID4, ConfigDict

from crewos_ingestion.contracts.context_contracts.caller_context import TenantId
from crewos_ingestion.contracts.input_contracts.source_lifecycle import SourceVersion

_MAX_VERSION = 2**63 - 1


class SourceVersionLimitReached(RuntimeError):
    """
    The source cannot advance beyond the highest supported version.
    """


class StoredSourceVersion(BaseModel):
    """
    One stored revision of a tenant-owned source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    tenant_id: TenantId
    source_id: UUID4
    version: SourceVersion
    title: str
    content: str
    source_reference: str | None

    def next_revision(self, *, title: str, content: str, source_reference: str | None) -> Self:
        """
        Prepare the next revision from already validated replacement content.
        """
        if self.version == _MAX_VERSION:
            raise SourceVersionLimitReached("Source version limit reached")

        return type(self)(
            tenant_id=self.tenant_id,
            source_id=self.source_id,
            version=self.version + 1,
            title=title,
            content=content,
            source_reference=source_reference,
        )
