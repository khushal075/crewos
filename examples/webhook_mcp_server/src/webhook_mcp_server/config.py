"""Load the one synthetic tenant assigned by the process launcher."""

import os
import re


def configured_tenant() -> str:
    tenant = os.environ.get("WEBHOOK_MCP_TENANT", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant):
        raise ValueError("A valid WEBHOOK_MCP_TENANT is required")
    return tenant
