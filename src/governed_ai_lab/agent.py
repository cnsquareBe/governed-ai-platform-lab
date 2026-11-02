"""Week 1 - a small agent that calls MCP tools directly, with no governance.

The loop is written by hand so every step is visible:
  1. connect to the MCP server and list its tools
  2. send the task + tool definitions to Claude
  3. Claude answers with tool_use blocks -> we call the MCP tool -> send the result back
  4. repeat until Claude stops asking for tools

Step 3 is the line the whole platform is about: right now the agent calls the tool
directly. From Week 2 on, a governed workflow sits in between.

Modes:
  claude   - Claude decides which tools to call (needs ANTHROPIC_API_KEY)
  offline  - a fixed script makes the same calls, so the lab runs without a key
  auto     - claude when credentials are configured, otherwise offline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from mcp import Client, StdioServerParameters
from mcp_types import CallToolResult

MODEL = "claude-opus-5-5"
AGENT_ID = "week1-agent"  # self-declared: nothing verifies this yet (Week 3 fixes that)
DEFAULT_TASK = "The payments service in prod is slow and customers see errors. Investigate and fix it."
SYSTEM_PROMPT = (
    "You are an operations agent. Use the tools to check service health before acting. "
    "Only restart a service when its status shows a problem, and always give a concrete reason. "
    "When you are done, summarise what you found, what you did, and the result in a few sentences."
)
LOG_FILE = Path(os.environ.get("LAB_LOG_DIR", "logs")) / "tool_calls.jsonl"


# --------------------------------------------------------------------------- tool calls


class ToolCaller:
    """Calls MCP tools and records every call to the console and to logs/tool_calls.jsonl.

    This is the agent's only way to touch the outside world, which is why later weeks
    replace it with the governed gateway.
    """

    def __init__(self, client: Client, mode: str) -> None:
        self.client = client
        self.mode = mode
        self.calls = 0
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        call_id = uuid.uuid4().hex[:8]
        print(f"\n  -> tool call [{call_id}] {name}({json.dumps(arguments)})")
        started = time.perf_counter()
        result = await self.client.call_tool(name, arguments)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        text = result_to_text(result)
        status = "ERROR" if result.is_error else "ok"
        print(f"  <- {status} in {duration_ms} ms: {text}")

        self.calls += 1
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "call_id": call_id,
            "agent_id": AGENT_ID,
            "mode": self.mode,
            "tool": name,
            "arguments": arguments,
            "is_error": bool(result.is_error),
            "result": text,
            "duration_ms": duration_ms,
        }
        with LOG_FILE.open("a") as f:
            f.write(json.dumps(record) + "\n")
        return text, bool(result.is_error)


def result_to_text(result: CallToolResult) -> str:
    if result.structured_content is not None:
        return json.dumps(result.structured_content)
    text = "\n".join(getattr(block, "text", str(block)) for block in result.content)
    try:
        return json.dumps(json.loads(text))  # compact one-line JSON
    except ValueError:
        return text


# --------------------------------------------------------------------------- Claude mode


async def run_with_claude(
    client: Client,
    caller: ToolCaller,
    task: str,
    max_turns: int,
    claude: anthropic.AsyncAnthropic | None = None,
) -> None:
    listed = await client.list_tools()
    tools = [
        {"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
        for t in listed.tools
    ]
    print(f"Tools offered to Claude: {', '.join(t['name'] for t in tools)}")

    claude = claude or anthropic.AsyncAnthropic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

    for turn in range(1, max_turns + 1):
        response = await claude.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
            output_config={"effort": "medium"},
            # If a safety classifier declines, the API retries on a suitable model in the same call.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )

        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"\nClaude: {block.text.strip()}")

        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            print(f"\nClaude declined the request (category: {category}). Stopping.")
            return
        if response.stop_reason == "max_tokens":
            print("\nThe response hit max_tokens before finishing. Stopping.")
            return
        if response.stop_reason != "tool_use":
            return  # end_turn: Claude is done

        # Keep the full assistant content (thinking + tool_use blocks) in the history.
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            # >>> No identity, policy, permission or approval check happens here. <<<
            text, is_error = await caller.call(block.name, dict(block.input))
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": is_error}
            )
        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": tool_results})

    print(f"\nStopped after {max_turns} turns.")


# --------------------------------------------------------------------------- offline mode


async def run_offline(caller: ToolCaller, service: str, env: str) -> None:
    """Same calls Claude would typically make, in a fixed order. No LLM involved."""
    print("Offline planner: check status, restart if unhealthy, check again.")
    text, is_error = await caller.call("get_service_status", {"service": service, "env": env})
    if is_error:
        print(f"\nSummary: could not read the status of {service}/{env}.")
        return
    status = json.loads(text)
    if status["health"] == "healthy":
        print(f"\nSummary: {service}/{env} is healthy. No action taken.")
        return

    reason = f"health={status['health']}, error_rate={status['error_rate_pct']}%, p95={status['p95_latency_ms']} ms"
    await caller.call("restart_service", {"service": service, "env": env, "reason": reason})
    text, _ = await caller.call("get_service_status", {"service": service, "env": env})
    after = json.loads(text)
    print(
        f"\nSummary: {service}/{env} was {status['health']} ({reason}). Restarted it. "
        f"Now {after['health']} with error rate {after['error_rate_pct']}%."
    )


# --------------------------------------------------------------------------- entry point


def has_claude_credentials() -> bool:
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")):
        return True
    return (Path.home() / ".config" / "anthropic").exists()


def server_target(server_url: str | None) -> StdioServerParameters | str:
    if server_url:
        return server_url  # Streamable HTTP, e.g. http://localhost:8000/mcp
    # Launch the ops server as a subprocess and talk to it over stdin/stdout.
    return StdioServerParameters(command=sys.executable, args=["-m", "governed_ai_lab.ops_server"])


async def run(args: argparse.Namespace) -> int:
    mode = args.mode
    if mode == "auto":
        mode = "claude" if has_claude_credentials() else "offline"
        if mode == "offline":
            print("No Anthropic credentials found, so running in offline mode. Set ANTHROPIC_API_KEY to use Claude.")

    transport = f"HTTP {args.server_url}" if args.server_url else "stdio subprocess"
    print(f"Agent '{AGENT_ID}' | mode: {mode} | MCP server via {transport}")
    print(f"Task: {args.task}")

    async with Client(server_target(args.server_url)) as client:
        caller = ToolCaller(client, mode)
        try:
            if mode == "claude":
                await run_with_claude(client, caller, args.task, args.max_turns)
            else:
                await run_offline(caller, args.service, args.env)
        except anthropic.AuthenticationError:
            print("\nThe Anthropic API rejected the credentials. Check ANTHROPIC_API_KEY, or run with --mode offline.")
            return 1
        except anthropic.RateLimitError:
            print("\nRate limited by the Anthropic API. Wait a moment and try again.")
            return 1
        except anthropic.APIStatusError as e:
            print(f"\nAnthropic API error {e.status_code}: {e.message}")
            return 1
        except anthropic.APIConnectionError:
            print("\nCould not reach the Anthropic API. Check your network, or run with --mode offline.")
            return 1

    print(f"\n{caller.calls} tool call(s) logged to {LOG_FILE}")
    return 0


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Week 1 agent: calls MCP tools with no governance")
    parser.add_argument("task", nargs="?", default=DEFAULT_TASK)
    parser.add_argument("--mode", choices=["auto", "claude", "offline"], default="auto")
    parser.add_argument("--server-url", default=os.environ.get("MCP_SERVER_URL"),
                        help="Streamable HTTP URL of the ops server; default launches it over stdio")
    parser.add_argument("--service", default="payments", help="offline mode: service to check")
    parser.add_argument("--env", default="prod", help="offline mode: environment")
    parser.add_argument("--max-turns", type=int, default=8)
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
