"""Versioned, tenant-aware relevance labels; authored judgments need human review."""

from typing import Annotated, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Key = Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Grade = Annotated[int, Field(strict=True, ge=1, le=3)]


class EvaluationDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Key
    tenant_id: Key
    title: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=8000)


class RelevanceQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: Key
    tenant_id: Key
    query: str = Field(min_length=1, max_length=2048)
    category: Key
    split: Literal["development", "holdout"]
    relevant: dict[Key, Grade] = Field(default_factory=dict)


class RelevanceDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    name: Key
    provenance: str = Field(min_length=1, max_length=2048)
    documents: tuple[EvaluationDocument, ...] = Field(min_length=1, max_length=1000)
    queries: tuple[RelevanceQuery, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def labels_match_scope(self) -> Self:
        docs = {d.id: d for d in self.documents}
        if len(docs) != len(self.documents) or len({q.id for q in self.queries}) != len(
            self.queries
        ):
            raise ValueError("Evaluation IDs must be unique")
        for query in self.queries:
            for key in query.relevant:
                if key not in docs or docs[key].tenant_id != query.tenant_id:
                    raise ValueError(
                        "Relevance labels must refer to existing same-tenant documents"
                    )
        return self
