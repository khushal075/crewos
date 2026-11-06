# Read-only webhook integration: contracts first

## Meaning of a delivery

A webhook delivery is the logical work of sending one event to a subscriber. The
external webhook service assigns its delivery ID. It can have several HTTP attempts.
It is not a CrewOS agent run ID, Celery task ID, incoming HTTP request ID or HTTP
response code. Different tenants may use the same opaque identifier; tenant scope
must participate in lookup. CrewOS will read the service's existing record.

## Current increment

`application/dtos/webhook_delivery_status.py` defines the input and discriminated
response contracts. Input accepts only a bounded opaque `delivery_id`. It cannot
supply a tenant, endpoint, permission assignment or credential. The initial ID
format permits ASCII letters/digits and `_.:-`, starts with a letter/digit and has
at most 128 characters. Adapters for incompatible provider ID formats would need
an explicit contract change; this format is not a universal webhook standard.

A found response has the same requested ID, a logical delivery status and an aware
`observed_at` timestamp. Lookup outcome answers whether a scoped readable record
exists; delivery status describes that record's execution state:

| Status | Required interpretation for future adapters |
| --- | --- |
| `pending` | Service reports the logical delivery waiting to be attempted. |
| `in_progress` | Service reports delivery work underway. |
| `retry_scheduled` | Service reports another attempt scheduled. |
| `succeeded` | Service reports recipient acknowledgment under its documented delivery semantics. This does not prove downstream business processing. |
| `failed` | Service reports terminal delivery failure, not merely one failed attempt. |
| `unknown` | A readable record exists, but no supported state mapping can be established. |

Missing and other-tenant records share `outcome=unavailable`, with no state or
ownership explanation. This avoids confirming another tenant's records. An upstream
outage, timeout or throttling must raise `WebhookServiceUnavailable`, not return
unavailable. Malformed or identity-mismatched responses raise `WebhookResponseInvalid`.
Those error messages must exclude credentials and upstream response bodies.

`application/interfaces/webhook_delivery_status_reader.py` defines a `Protocol`
requiring explicit tenant scope, delivery ID and timeout. Its documentation specifies
read-only behavior, result identity preservation and provider failure semantics.
It does not itself authenticate, authorize, isolate storage, or enforce timeouts.
Concrete adapters and the handler must implement those obligations. A network
transport timeout is also not a guarantee of total execution duration.

## Subsequent increments

1. A fixture adapter keyed by tenant plus delivery ID, to prove scope and state mapping.
2. A handler with a dedicated permission and the existing policy enforcement gate.
3. Tool registration and controlled agent-loop tests.
4. An HTTP adapter with trusted endpoint/credential configuration, timeouts, bounded
   responses, explicit state mapping and sanitized error handling.
5. MCP/skills integration and later approval-controlled writes as separate work.

No handler, adapter, endpoint, permission grant or model-facing tool is added by
this contract-only increment. No network calls or external writes occur.
