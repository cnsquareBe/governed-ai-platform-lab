# Week 1: explain it in 2 minutes

## The script (about 2 minutes out loud)

> An AI agent doesn't act on systems by itself. It works through **tools**: small functions with a name, a description and a typed list of parameters.
>
> **MCP**, the Model Context Protocol, is the open standard for publishing those tools. A system, like our ops platform, runs an **MCP server** that says: "I have `get_service_status` and `restart_service`, and here is how to call them."
>
> The agent connects, lists the tools, and sends the user's task plus those tool descriptions to the model. The model doesn't run anything. It replies with a **tool_use request**: "please call `restart_service` with service=payments, env=prod, reason=error rate 7%."
>
> The **agent code** then decides whether to execute that request. If it does, it sends the result back to the model, and the loop continues until the model says it's done.
>
> So there are three parties: the model **proposes**, the agent **decides and executes**, the MCP server **acts**. The important point: the decision point is ordinary code we control. Today it executes every request blindly. Any agent, any service, any environment, no approval, and a log file anyone can edit.
>
> That single line of code is where the governed platform plugs in. Over the next weeks we'll replace "execute blindly" with identity, policy, permission, approval, validation and audit.

## Draw this on a whiteboard

```
User task ─▶ Agent ─(task + tool schemas)─▶ Model
                ▲                              │
                └──────(tool_use request)──────┘
                │
                ▼  ← governance goes HERE (Weeks 2-7)
          MCP server ─▶ real system
```

## Questions you'll get

**"So the AI can restart production by itself?"**
In Week 1, yes. That's the point of the demo. The model only asks; our code executes. The platform makes that code refuse, pause for approval, or allow based on rules.

**"Why MCP rather than calling APIs directly?"**
One standard way to describe and call tools, whatever the agent framework or model. It also gives us one place to intercept every call: the gateway in front of the MCP servers.

**"Can the model call a tool that isn't listed?"**
It can only ask for tools it was given. If it invents a name or bad arguments, the call fails and the error goes back to the model. That's still a request, never direct access.

**"What does `destructiveHint` do?"**
It's a hint published by the tool author. Useful input for policy (Week 4), but never trusted on its own: the server decides what it declares, so policy uses our own tool registry.

**"Where's the audit trail?"**
`logs/tool_calls.jsonl`, one line per call. It's a start, but it's not trustworthy: it's a local file, the agent writes it about itself, and anyone can edit it. Week 7 fixes that.
