"""Week 1 tests. They run without an API key and without containers.

- The MCP server runs in-process (mcp.Client accepts the server object directly).
- Claude is replaced by a fake HTTP transport that replays canned API responses,
  so the agent loop is tested exactly as written.
"""

import copy
import json

import anthropic
import httpx2
import pytest
from mcp import Client

from governed_ai_lab import agent, ops_server

INITIAL_STATE = copy.deepcopy(ops_server.SERVICES)


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    ops_server.SERVICES.clear()
    ops_server.SERVICES.update(copy.deepcopy(INITIAL_STATE))
    monkeypatch.setattr(agent, "LOG_FILE", tmp_path / "tool_calls.jsonl")


def log_lines():
    return [json.loads(line) for line in agent.LOG_FILE.read_text().splitlines()]


async def test_server_lists_both_tools_with_hints():
    async with Client(ops_server.server) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"get_service_status", "restart_service"}
    assert tools["get_service_status"].annotations.read_only_hint is True
    assert tools["restart_service"].annotations.destructive_hint is True


async def test_offline_agent_restarts_degraded_service_and_logs_every_call():
    async with Client(ops_server.server) as client:
        await agent.run_offline(agent.ToolCaller(client, "offline"), "payments", "prod")

    assert ops_server.SERVICES[("payments", "prod")]["health"] == "healthy"
    assert [r["tool"] for r in log_lines()] == ["get_service_status", "restart_service", "get_service_status"]


async def test_offline_agent_leaves_healthy_service_alone():
    async with Client(ops_server.server) as client:
        await agent.run_offline(agent.ToolCaller(client, "offline"), "checkout", "prod")

    assert [r["tool"] for r in log_lines()] == ["get_service_status"]


async def test_unknown_service_comes_back_as_tool_error():
    async with Client(ops_server.server) as client:
        text, is_error = await agent.ToolCaller(client, "offline").call(
            "get_service_status", {"service": "billing", "env": "prod"}
        )
    assert is_error
    assert "Unknown service" in text


def fake_message(content, stop_reason):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": agent.MODEL,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


async def test_claude_loop_executes_tool_calls_and_sends_results_back():
    replies = [
        fake_message([{"type": "tool_use", "id": "tu_1", "name": "get_service_status",
                       "input": {"service": "payments", "env": "prod"}}], "tool_use"),
        fake_message([{"type": "tool_use", "id": "tu_2", "name": "restart_service",
                       "input": {"service": "payments", "env": "prod", "reason": "error rate 7.2%"}}], "tool_use"),
        fake_message([{"type": "text", "text": "Restarted payments; it is healthy now."}], "end_turn"),
    ]
    requests = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append((request, json.loads(request.content)))
        return httpx2.Response(200, json=replies[len(requests) - 1])

    claude = anthropic.AsyncAnthropic(
        api_key="test-key",
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    async with Client(ops_server.server) as client:
        await agent.run_with_claude(client, agent.ToolCaller(client, "claude"), "fix payments", 8, claude=claude)

    assert len(requests) == 3
    first_request, first_body = requests[0]
    assert first_body["model"] == "claude-opus-5-5"
    assert first_body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in first_request.headers["anthropic-beta"]
    assert {t["name"] for t in first_body["tools"]} == {"get_service_status", "restart_service"}

    # The second request carries the result of the first tool call, matched by tool_use_id.
    tool_result = requests[1][1]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "tu_1"
    assert json.loads(tool_result["content"])["health"] == "degraded"

    assert ops_server.SERVICES[("payments", "prod")]["health"] == "healthy"
    assert [r["tool"] for r in log_lines()] == ["get_service_status", "restart_service"]
