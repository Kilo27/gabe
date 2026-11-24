# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

Shipped as a desktop app on macOS and Windows (native window around the web UI); never used in a browser.

## Stack

React + TypeScript (Vite) single-page app in `web/`, served by a FastAPI service in the
`gabe` package, wrapped in a pywebview desktop app packaged with PyInstaller (`gabe app`,
`packaging/macos/build.sh`). Chosen by the user, matching `docs/UI_PLAN.md`. Storage stays
on the existing JSON files under `data/` for now; Postgres is a later phase.

## Users

Primary: **admin and ops staff** who work Gabe's task list, check what Gabe did, fix
mistakes and approve the actions Gabe wants to take. They are not technical: they should
never need to read YAML, JSON, tool names or logs to do their job.

Secondary: leaders and managers (read the daily brief, ask Gabe for things, approve
sends) and whoever sets Gabe up (connects Gmail, writes playbooks, sets approval rules).

## Product Purpose

Gabe is an AI chief of staff that takes admin and management work off people's plates:
it tracks tasks and follow-ups, remembers decisions, drafts and sends communications,
writes a daily brief and hands focused work to specialist sub-agents, all under an
approval policy each company sets. The web app is the front door for everything the
terminal does today, with the approval queue at its centre.

Success: a non-technical admin can open the app, see what needs them today, approve or
fix Gabe's work and change what Gabe is allowed to do, without help.

## Positioning

Approval-first. Gabe acts, but anything risky waits for a person, and every answer links
back to the actions behind it. Everything company-specific (playbooks, policy,
integrations, specialists) is configuration a person can edit, not code.

## Operating Context

- Daily rhythm: the brief is written at `briefing.time` on working days and emailed.
- Gmail / Google Workspace is the connected mailbox.
- Policy decisions per action: allow (just do it), ask (wait for a person), deny (never).
- Every action Gabe takes is recorded in `data/audit.jsonl`.

## Capabilities and Constraints

- Tools today: current time, notes (remember / recall / forget), tasks (create / list /
  update), email (search / read / draft / send / mark read), delegation to specialists.
- Approvals wait for a person while the web server is running. A server restart expires
  any approval still waiting (Gabe is told it was not approved).
- Chat history is kept on disk; Gabe's working memory of a conversation resets when the
  server restarts.
- Undecided: hosting, single- vs multi-tenant, sign-in and roles (no auth yet; the app is
  for a trusted local network).

## Evidence on Hand

Real data in `data/` (audit log, a daily brief). No customers, testimonials or pricing
exist; never invent them.

## Product Principles

1. Approval-first: the queue of things waiting for a person is never more than one click away.
2. Show the work: every answer can be traced to what Gabe actually did.
3. Plain words: no tool names, IDs or config jargon in the main path; translate them.
4. Config is product: anything in `gabe.yaml` that a company changes is editable in the app.

## Accessibility & Inclusion

Non-technical users. WCAG 2.2 AA contrast, full keyboard use, colour never the only
signal (every colour code also carries a label or icon).
