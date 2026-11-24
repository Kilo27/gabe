# Gabe UI: plan

Status: **plan only**, not built yet. Today Gabe runs from the terminal
(`gabe`, `gabe brief`, `gabe schedule`).

## Who uses it

| Person | Needs |
|---|---|
| Leaders and managers | See today's brief, ask Gabe for things, approve what Gabe wants to send |
| Admin / ops staff | Work the task list, check what Gabe did, fix mistakes |
| Whoever sets Gabe up | Connect systems, write playbooks, set approval rules, add specialists |

## Principles

1. **Approval-first.** Most real value comes from actions Gabe proposes and a person
   approves. The approval queue is the centre of the product, not a modal popup.
2. **Show the work.** Every answer links to the tool calls behind it, so people can
   see why Gabe said something.
3. **Config is product.** Playbooks, policy and integrations are edited in the UI and
   versioned, not only in YAML.
4. **One backend, many front doors.** The web app, Slack (future) and email briefings
   all use the same API and the same approval queue.

## Screens

### 1. Today (home)
- The latest daily brief, rendered, with each item linked to its task or email.
- Pending approvals count with one-click jump.
- Quick actions: "Ask Gabe", "Regenerate brief".

### 2. Chat
- Conversation with Gabe, with responses streamed as they're written.
- Tool activity shown inline and collapsible ("Searched email: is:unread", "Created task").
- When Gabe hits an action that needs approval, an approval card appears in the chat
  and in the Approvals queue.
- Conversations persist and can be resumed. Optionally shared with a colleague.

### 3. Approvals
- Queue of pending actions (e.g. send_email). Each shows who or what triggered it, the
  full content, and why Gabe wants to do it.
- **Approve**, **Edit then approve** (e.g. change the email text), **Reject with note**.
  The note goes back to Gabe.
- History of decided approvals.

### 4. Tasks
- Table and board views (open / in progress / blocked / done), filtered by owner,
  project and due date.
- Inline edit. Each task shows its source (which meeting, email or chat created it).

### 5. Inbox (once Gmail is connected)
- Gabe's triage of recent email: needs reply / FYI / ignore, with suggested owner.
- One-click "Draft a reply" (creates a Gmail draft via `draft_email`).

### 6. Memory
- Searchable notes (decisions, people, preferences). Edit or delete wrong ones.

### 7. Activity
- The audit log (`data/audit.jsonl`) as a filterable timeline: agent, tool, outcome,
  input. Export to CSV.

### 8. Settings
- **Company:** profile, timezone, facts.
- **Playbooks:** markdown editor with preview, versioned.
- **Integrations:** connect or disconnect (Gmail OAuth button replaces `gabe auth gmail`),
  per-integration settings, health status.
- **Policy:** matrix of tool × allow / ask / deny, with risk defaults.
- **Specialists:** add or edit delegates and choose their tools.
- **Briefing:** time, days, recipients (once delivery channels exist).
- **Model:** provider (Anthropic / local GLM), model, effort. Shows token usage and cost.

## Architecture

```
React + TypeScript (Vite) SPA ──HTTP/SSE──► FastAPI service ──► gabe core (unchanged API:
                                              │                  build_gabe, Agent, briefing)
Slack app (future) ─────────────────────────►│
                                              ├─ Postgres: sessions, messages, tasks, notes,
                                              │            approvals, audit, config versions
                                              └─ worker: scheduler (briefings), long runs
```

- **Backend:** FastAPI (Python, same process space as the core). Server-sent events
  stream agent events (`step`, `tool`, `thought`) and text to the browser.
- **Frontend:** React + TypeScript, Vite, Tailwind with a component kit (e.g. shadcn/ui),
  TanStack Query.
- **Auth:** Google Workspace SSO (matches the Gmail choice). Roles: admin, member, viewer.

### Core changes the UI needs
1. **Asynchronous approvals.** Today the approver is a blocking callback (terminal
   prompt). The UI needs a run to *pause* at an approval, store the pending tool call,
   and *resume* when someone decides, possibly hours later. That means persisting
   conversation state (provider history and pending calls) and adding
   `Agent.resume(decisions)`. This is the biggest change.
2. **Persistent sessions** in Postgres instead of in-memory conversations.
3. **Storage swap.** Move tasks, memory and audit from JSON files to Postgres-backed
   integrations, with the same tool names.
4. **Config in the database** with versioning. YAML stays as import/export.
5. **Per-user identity** passed into tools, so the audit log says who asked and approvals
   respect roles.

## Phases

| Phase | Scope | Rough size |
|---|---|---|
| 1 | FastAPI service, async approvals, persistent sessions; screens: Today, Chat, Approvals | largest; core changes |
| 2 | Tasks, Memory, Activity screens; Postgres storage | medium |
| 3 | Settings (integrations with Gmail OAuth, policy, playbooks, specialists, model) | medium |
| 4 | Inbox triage view | small–medium |
| 5 | Slack app on the same API and approval queue (see roadmap) | medium |

## Open questions
- Hosting: internal server or VM alongside the future GLM box, or cloud?
- One Gabe per company (single tenant), or a hosted product serving many companies?
  This changes auth, data isolation and config storage.
- Who can approve what? Approval rights per tool or per person.
- Mobile: responsive web is enough at first, or are approvals on the phone a must-have?
  If so, Slack covers much of it.
