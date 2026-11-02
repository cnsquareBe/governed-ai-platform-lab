"""Week 1 - a mock "operations" MCP server.

It pretends to manage a few services so the agent has something real-looking to act on.
Two tools:
  - get_service_status  (read-only)
  - restart_service     (changes state)

There is NO governance here on purpose: any client that can reach this server can restart
anything. Weeks 2-7 put the governance chain between the agent and this server.

Run it:
  stdio (default, launched by the agent):  uv run ops-server
  HTTP (for containers / MCP Inspector):   uv run ops-server --http --port 8000
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations

# In-memory fake fleet. State lives as long as the server process.
# "payments" in prod starts degraded so the agent has an incident to fix.
SERVICES: dict[tuple[str, str], dict] = {
    ("payments", "prod"): {"health": "degraded", "error_rate_pct": 7.2, "p95_latency_ms": 1840, "replicas": 3},
    ("payments", "staging"): {"health": "healthy", "error_rate_pct": 0.3, "p95_latency_ms": 210, "replicas": 1},
    ("checkout", "prod"): {"health": "healthy", "error_rate_pct": 0.4, "p95_latency_ms": 260, "replicas": 4},
    ("search", "prod"): {"health": "healthy", "error_rate_pct": 0.1, "p95_latency_ms": 95, "replicas": 2},
}

server = MCPServer(
    name="ops-server",
    instructions="Mock operations tools for the Governed AI lab. Services: payments, checkout, search. "
    "Environments: prod, staging.",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _lookup(service: str, env: str) -> dict:
    key = (service.lower(), env.lower())
    if key not in SERVICES:
        known = ", ".join(f"{s}/{e}" for s, e in SERVICES)
        # ToolError = an expected failure whose message the agent may see (other exceptions stay hidden).
        raise ToolError(f"Unknown service '{service}' in env '{env}'. Known: {known}")
    return SERVICES[key]


@server.tool(
    annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False),
)
def get_service_status(service: str, env: str = "prod") -> dict:
    """Return the current health of a service: health, error rate (%), p95 latency (ms) and replicas.

    Args:
        service: Service name, for example "payments".
        env: Environment, "prod" or "staging".
    """
    state = _lookup(service, env)
    return {"service": service, "env": env, "checked_at": _now(), **state}


@server.tool(
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False),
)
def restart_service(service: str, env: str, reason: str) -> dict:
    """Restart all replicas of a service. This changes production state.

    Args:
        service: Service name, for example "payments".
        env: Environment, "prod" or "staging".
        reason: Why the restart is needed. Recorded with the action.
    """
    state = _lookup(service, env)
    before = dict(state)
    # Pretend the restart clears the fault.
    state.update(health="healthy", error_rate_pct=0.5, p95_latency_ms=230)
    return {
        "service": service,
        "env": env,
        "restarted_at": _now(),
        "reason": reason,
        "before": before,
        "after": dict(state),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock ops MCP server")
    parser.add_argument("--http", action="store_true", help="serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.http:
        server.run(
            "streamable-http",
            host=args.host,
            port=args.port,
            # Only accept requests addressed to these hosts (DNS-rebinding protection).
            transport_security=TransportSecuritySettings(
                allowed_hosts=["localhost:*", "127.0.0.1:*", "ops-server:*"],
                allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
            ),
        )
    else:
        server.run()


if __name__ == "__main__":
    main()
