"""
Inputs for reading, updating and deleting registered source.
"""

from typing import Annotated
from pydantic import UUID4, BaseModel, ConfigDict, Field
from crewos_ingestion.contracts.input_contracts.source_registration import SourceSubmission


SourceVersion = Annotated[int, Field(strict=True, ge=1, le=2**63 - 1)]


class SourceReadRequest(BaseModel):
    """
    Read the latest source version or request a specific version.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    source_id: UUID4
    version: SourceVersion | None = None


class SourceUpdateRequest(SourceSubmission):
    """
    Replace the source content if its latest version matches the caller's.
    """

    source_id: UUID4
    expected_version: SourceVersion


class SourceDeleteRequest(BaseModel):
    """
    Delete a source by ID within the verified tenant's scope.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    source_id: UUID4
