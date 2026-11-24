# Architecture

```
            ┌──────────── config/gabe.yaml + playbooks/*.md ────────────┐
            │ company profile · provider · integrations · policy · delegates
            └───────────────────────────────┬───────────────────────────┘
                                            │ build_gabe()  (gabe/app.py)
                                            ▼
 CLI / (future: Slack, email, scheduler) ─► Agent "Gabe" (gabe/agent.py)
                                            │  loop: provider.step() → tool calls
                                            │        → validate → policy → approver → run
                                            │        → results back → repeat
                    ┌───────────────────────┼─────────────────────────┐
                    ▼                       ▼                         ▼
          Provider (gabe/providers)   ToolRegistry (gabe/tools)    AuditLog
          ├ anthropic  (Claude API)    ├ integrations/* tools       data/audit.jsonl
          └ openai_compatible (GLM)    └ delegate → sub-Agent with
                                         restricted tool subset,
                                         same policy & approver
```

## Key decisions

**We own the agent loop.** Gabe uses a manual loop over the Messages API rather than a
hosted agent runtime, so the same loop drives Claude today and a local GLM later. That
keeps company data on-prem once the switch happens.

**Providers own their conversation state.** `Provider.start()` returns a `Conversation`
that keeps history in its native wire format. For Claude this matters: assistant content
(including thinking and fallback blocks) is stored exactly as returned and resent
unchanged, append-only. The agent loop only sees provider-neutral `StepResult` /
`ToolCall` / `ToolResult`.

**Claude settings** (`gabe/providers/anthropic_provider.py`): `claude-opus-5-5`, adaptive
thinking (the model default), `output_config.effort` from config (default `high`),
streaming with `max_tokens` 64k, top-level prompt caching, and server-side refusal
fallbacks (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`). Turn that off
with `refusal_fallback: false`. Tools stream their inputs eagerly, so every input is
validated client-side before it runs. Tool calls from a truncated (`max_tokens`) turn
are never run.

**Prompt stability.** The system prompt is built once from config and contains no
timestamps. The tool list is sorted. Both keep the cache warm. Time comes from the
`get_current_time` tool.

**Governance.** Each tool has a risk level. `Policy` maps risk, integration or tool name
to `allow`, `ask` or `deny`. `ask` calls an `Approver`: the terminal prompt in the CLI,
`deny_all` for unattended runs, and later a Slack or web approval flow. Denials go back to
the model as tool errors with an instruction not to retry. Every attempt is audited.

**Delegation.** Specialists defined in config become a `delegate` tool. Each specialist
is a fresh `Agent` with its own prompt and a tool subset. It shares the provider, policy,
approver and audit log, and it returns a report to Gabe.

## Roadmap

Done: Gmail access (search, read, draft, send), daily briefing (`gabe brief`, `gabe schedule`).

1. **Web UI**: planned in [UI_PLAN.md](UI_PLAN.md). Needs async approvals and persistent sessions first.
2. **Slack integration**: talk to Gabe in DMs and channels, approve actions with Slack
   buttons, post the daily brief to a channel. Built on the same API and approval queue
   as the web UI.
3. More connectors: Google Calendar and Docs, HRIS, ticketing. MCP client support so
   existing MCP servers plug in as integrations.
4. Durable storage (Postgres) behind the memory, tasks and audit integrations.
5. Local inference: serve GLM 5.3 with vLLM/SGLang, run the test suite and an eval set
   against both providers, then cut over.
