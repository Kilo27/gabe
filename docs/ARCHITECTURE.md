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
          └ openai_compatible (GLM)    └ delegate → team member's   (with the chain
                                         Agent (gabe/team.py):        of hand-offs)
                                         own skills, own direct
                                         reports, policy ≤ company
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

**Delegation is a hierarchy.** The team (`team_file`, see `gabe/team.py`) is a tree under
Gabe. Every agent with active direct reports gets its own `delegate` tool whose enum
lists only those reports, so work flows strictly down the org chart. Each hand-off
builds a fresh `Agent` with the member's prompt (job, manager, its own roster), its
skill subset, and `policy.restricted(freedom ceiling)`. The ceiling can only tighten a
decision (`stricter()` of company rule and freedom level). Agents carry a `path`
(Gabe › Operations lead › Scheduler) that goes into every event and audit record.
Validation refuses loops, unknown managers and chains deeper than `MAX_DEPTH` (4).
Paused members drop out with their whole branch.

**Gabe dispatches.** When Gabe has direct reports it gets a dispatcher prompt and only
`assistant.own_tools` plus `delegate`, so the team does the work. Tools no working member
covers (`team_coverage`) stay with Gabe, so pausing a branch never strands an action. The
briefing agent is built separately with the hands-on prompt and read-only tools.

**MCP servers.** The `mcp` integration turns each configured MCP server's tools into
Gabe tools named `<server>__<tool>`, with `integration` set to the server name so policy
works per server. Risk defaults to `external` (server annotations are untrusted hints).
The loop is synchronous and the registry is rebuilt per run, so one background asyncio
loop in a daemon thread holds every session open for the life of the process; `setup`
reuses live sessions keyed by a hash of each server's connection config, reconnects dead
ones, closes removed ones, and skips (logs) servers that fail. Status per server shows
in Settings → Connections.

**Changing an installation.** An installation is its config folder; Gabe's code never
changes. Companies extend it with local integration files (`integrations/<name>.py`,
re-imported when they change; failures are skipped and reported) and edit it with Claude
Code, either in a Terminal opened from Settings or through the `change_gabe` action
(`gabe/integrations/claude_code.py`), which runs `claude -p --restricted` in the folder
under an `ask_first` approval, with a backup, `gabe check` (`gabe/check.py`) afterwards,
one fix-up round, and automatic undo. It runs on Gabe's own provider (same key and
model; for an OpenAI-compatible GLM server, the same server's Anthropic-format API), so
self-editing moves to the local model along with everything else. Claude Code is optional: nothing else depends on it,
so a local-model deployment loses only this action.

**Desktop app.** The team is edited and watched from the app's Agents screen
(`gabe/web/server.py`, `/api/agents`); see the README's Desktop app section. Approvals block
the run's thread until someone decides in the app (approve, edit then approve, or decline
with a note); ones still waiting when the app quits expire and Gabe is told no.

## Roadmap

Done: Gmail access (search, read, draft, send), daily briefing (`gabe brief`, `gabe schedule`).

1. **Web UI**: planned in [UI_PLAN.md](UI_PLAN.md). Needs async approvals and persistent sessions first.
2. **Slack integration**: talk to Gabe in DMs and channels, approve actions with Slack
   buttons, post the daily brief to a channel. Built on the same API and approval queue
   as the web UI.
3. More connectors. Done: MCP client (`gabe/integrations/mcp.py`), so any MCP server
   (Calendar, Drive, Slack, Notion, Linear...) plugs in as integrations under the same
   policy. Next: OAuth for remote MCP servers (bearer headers only today), then native
   connectors where MCP falls short (HRIS, ticketing).
4. Durable storage (Postgres) behind the memory, tasks and audit integrations.
5. Local inference: serve GLM 5.3 with vLLM/SGLang, run the test suite and an eval set
   against both providers, then cut over.
