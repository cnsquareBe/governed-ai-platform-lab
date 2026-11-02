# Governed AI Action Platform: hands-on lab

A 7-week lab that builds the governed action chain one layer at a time:
Identity, Context, Policy, Permission, Action, Validation, Audit.

| Week | Layer | Status |
|---|---|---|
| 1 | Agent calls MCP tools directly, **no governance** | ✅ this folder |
| 2 | Temporal workflow + human approval | next |
| 3 | Keycloak identity, gateway rejects unknown agents | |
| 4 | OPA risk-tier policy | |
| 5 | OpenFGA permissions + OpenBao 5-minute credentials | |
| 6 | Prometheus validation + automatic rollback | |
| 7 | OpenTelemetry + Langfuse end-to-end trace | |

---

## Week 1: how an agent calls a tool

```
 ┌──────────────┐  1. list_tools      ┌──────────────────────┐
 │    agent     │ ──────────────────▶ │  ops-server (MCP)     │
 │              │  3. call_tool       │  get_service_status   │
 │  (your code) │ ──────────────────▶ │  restart_service      │
 └──────┬───────┘                     └──────────────────────┘
        │ 2. task + tool schemas  ▲ tool_use blocks
        ▼                         │
 ┌──────────────┐                 │
 │    Claude    │ ────────────────┘
 └──────────────┘
```

1. The agent connects to the MCP server and asks which tools exist (name, description, JSON schema).
2. It sends the task and those schemas to Claude. Claude replies with `tool_use` blocks: *"call `restart_service` with these arguments"*.
3. The agent calls the tool and sends the result back to Claude. It loops until Claude stops asking.

**The model never touches the system itself.** It only *asks*. The agent code decides whether to execute.
In Week 1 it executes everything. That one line (`caller.call(...)` in [agent.py](src/governed_ai_lab/agent.py)) is where the whole governance chain will be inserted.

### Files

| File | What it is |
|---|---|
| [src/governed_ai_lab/ops_server.py](src/governed_ai_lab/ops_server.py) | MCP server with two mock tools and a fake fleet; `payments/prod` starts degraded |
| [src/governed_ai_lab/agent.py](src/governed_ai_lab/agent.py) | The agent: a hand-written tool-use loop with Claude, plus an offline mode |
| [tests/test_week1.py](tests/test_week1.py) | 5 tests, no API key or containers needed |
| [docs/week1-explain.md](docs/week1-explain.md) | Your 2-minute explanation and likely questions |
| `logs/tool_calls.jsonl` | Every tool call, one JSON line each (created on first run) |

---

## Setup

You need [uv](https://docs.astral.sh/uv/) (installed) and, for the container part, Rancher Desktop.

```bash
uv sync
```

Install Rancher Desktop (needs your admin password the first time it starts):

```bash
brew install --cask rancher
```

In Rancher Desktop → **Preferences → Container Engine**, choose **dockerd (moby)** so the `docker` and `docker compose` commands work. Kubernetes can stay off until Phase 2.

Optional, to use Claude instead of the offline planner:

```bash
cp .env.example .env
```

Then put your key in `.env` (`ANTHROPIC_API_KEY=...`). Without a key the agent runs in offline mode automatically.

## Run it

Offline (no key needed). A fixed script makes the calls:

```bash
uv run agent --mode offline
```

With Claude. Claude decides which tools to call:

```bash
uv run agent "The payments service in prod is slow and customers see errors. Investigate and fix it."
```

Watch the log of tool calls:

```bash
tail -f logs/tool_calls.jsonl
```

Run the tests:

```bash
uv run pytest -q
```

## Run it in containers (Rancher Desktop)

Start the MCP server over HTTP:

```bash
docker compose up -d --build ops-server
```

Run the agent against it:

```bash
docker compose run --rm agent
```

Stop everything:

```bash
docker compose down
```

With the **containerd** engine instead, replace `docker` with `nerdctl` in these commands.

Inspect the server visually with MCP Inspector (needs Node.js). Connect it to `http://localhost:8000/mcp` with transport *Streamable HTTP*:

```bash
npx @modelcontextprotocol/inspector
```

---

## Week 1 exercises

Do these and note what you see in the tracker's *Evidence* column.

1. **Read the tool schemas.** Open MCP Inspector and look at what Claude receives: descriptions, parameters and the `readOnlyHint` / `destructiveHint` annotations.
2. **Change the task.** Ask about `checkout` (healthy). Does Claude restart it? Why not? Which line in the system prompt matters?
3. **Break a tool.** Ask about `billing` (doesn't exist). The tool returns an error result, and Claude sees it and adapts.
4. **Spot the gaps.** Run `uv run agent "Restart search in prod"`. It restarts a healthy production service just because it was asked. List what should have stopped it. That list is Weeks 2 to 7.

## What is missing on purpose

| Gap you can see in Week 1 | Fixed in |
|---|---|
| A crash mid-action loses all state; nothing waits for a human | Week 2 (Temporal + approval) |
| `agent_id` is self-declared; anyone can call the server | Week 3 (Keycloak) |
| Nothing says *"restarting prod needs approval"* | Week 4 (OPA) |
| Every agent can touch every service; no credentials at all | Week 5 (OpenFGA, OpenBao) |
| The restart is assumed to have worked | Week 6 (Prometheus, rollback) |
| The log is a local file anyone can edit | Week 7 (OpenTelemetry, hash chain) |

## Notes

- The agent uses the Anthropic SDK directly, with a hand-written loop, so every step is visible. A framework such as LangGraph hides this loop; it can come later once the mechanics are clear.
- Model: `claude-opus-5-5`. Server-side refusal fallbacks are on (`fallbacks="default"`): if a safety classifier declines a request, the API retries on a suitable model in the same call. Remove the `betas` and `fallbacks` lines in `agent.py` to turn this off.
- Troubleshooting: if the agent container can't write `logs/`, run `chmod 777 logs` once on your Mac.
