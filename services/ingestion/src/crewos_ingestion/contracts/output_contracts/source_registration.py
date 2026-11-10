"""
Receipt returned by registration after a source version is stored.
"""

from typing import Literal

from pydantic import UUID4, BaseModel, ConfigDict, Field

class SourceAcceptanceReceipt(BaseModel):
    """
    Confirms source storage; Does not indicate search availability.
    """
    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)
    source_id: UUID4
    version: int = Field(strict=True, ge = 1)
    status: Literal["accepted"] = "accepted"