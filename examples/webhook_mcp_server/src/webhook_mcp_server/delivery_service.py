"""Immutable demo observations; request data never chooses the tenant."""

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryService:
    tenant: str

    def read(self, delivery_id: str) -> dict[str, str]:
        if (self.tenant, delivery_id) != ("tenant_a", "only_a"):
            return {"outcome": "unavailable", "delivery_id": delivery_id}
        return {
            "outcome": "found",
            "delivery_id": delivery_id,
            "status": "succeeded",
            "observed_at": "2026-11-01T00:00:00Z",
        }
