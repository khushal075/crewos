"""
Result returned after successful source lifecycle operation.
"""

from typing import Literal
from pydantic import UUID4, BaseModel, ConfigDict

from crewos_ingestion.contracts.input_contracts.source_lifecycle import SourceVersion


class SourceReadResult(BaseModel):
    """
    The Stored content and metadata of one source version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: UUID4
    version: SourceVersion
    title: str
    content: str
    source_reference: str | None


class SourceUpdateReceipt(BaseModel):
    """
    Identify the new version after storage commits the update.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: UUID4
    version: SourceVersion
    status: Literal["updated"] = "updated"


class SourceDeleteReceipt(BaseModel):
    """
    Confirm the source was removed from normal ingestion access.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    source_id: UUID4
    status: Literal["deleted"] = "deleted"
