# Gabe

Gabe is an agentic **chief of staff**: an AI that takes admin and management work off
people's plates. It tracks tasks and follow-ups, remembers decisions, drafts and sends
communications, and hands focused work to specialist sub-agents. All of it runs under
an approval policy that each company sets.

It's built to fit into any company's existing processes. Everything company-specific
lives in configuration, playbooks and pluggable integrations, not in the core.

> **Status:** early scaffold (v0.1). Runs today on the Anthropic API (Claude Opus 5.5).
> The goal is to run fully on-prem on GLM 5.3 served from a local GPU box. The provider
> layer is already in place for that.

## Install on a Mac

Paste this into Terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Kilo27/gabe/main/install.sh | sh
```

It downloads the latest build, puts Gabe in Applications and opens it. Nothing else to install;
Gabe keeps itself up to date after that. Needs an Apple silicon Mac (M1 or later). No admin
rights needed: on a standard account it goes in your own Applications folder (`~/Applications`).

Or download **Gabe.dmg** from the [mac-latest release](https://github.com/Kilo27/gabe/releases/tag/mac-latest)
and drag Gabe to Applications. Until builds are notarized (see below), macOS blocks the first
open: System Settings → Privacy & Security → **Open Anyway**. The Terminal command avoids that.

## Quick start (from source)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,gmail]'
export ANTHROPIC_API_KEY=...        # or: ant auth login
gabe                                # interactive, uses config/gabe.yaml
gabe -m "Give me the weekly brief"  # one-shot
gabe brief                          # today's daily briefing (saved to data/briefings/)
gabe schedule                       # run the briefing every weekday at the configured time
gabe auth gmail                     # connect Gmail (see docs/GMAIL_SETUP.md)
gabe auth calendar                  # connect Google Calendar (same doc)
gabe slack                          # run the Slack FAQ bot on its own (see docs/FAQ_BOT.md)
gabe --unattended -m "..."          # refuses anything that needs approval
pytest                              # tests use a scripted provider, no API calls
```

## Desktop app (macOS and Windows)

Gabe ships as a desktop app, not a website. It runs its own local service inside a native
window; that service only accepts calls from its own window (a fresh token each launch).

```bash
pip install -e '.[desktop,gmail,build]'
packaging/macos/build.sh        # → dist/macos/Gabe.app, Gabe.dmg, Gabe-mac.zip, installed to /Applications (needs Node)
gabe app                        # run it from source instead
```

The **Mac app** GitHub Actions workflow builds it on every PR and push to `main`; pushes to `main`
replace the files on the `mac-latest` release, which the installer and installed copies download.
Optional repo secrets:

- `MACOS_CERT_P12` / `MACOS_CERT_PASSWORD`: the signing identity. Run
  `packaging/macos/upload_signing_cert.sh` once to use your "Gabe Local Signing" certificate, so
  every build has the same signature and updates keep Gabe's Keychain and notification
  permissions. Without it builds are ad-hoc signed and macOS asks again after each update.
- A Developer ID certificate in those secrets plus `APPLE_ID`, `APPLE_TEAM_ID` and
  `APPLE_APP_PASSWORD` (an app-specific password): builds are notarized and the .dmg opens with
  no warning.
- `GOOGLE_OAUTH_CLIENT_JSON`: Sign in with Google.

Windows: `packaging\windows\build.ps1` on a Windows PC (needs Python, Node, Inno Setup 6) gives
`dist\windows\GabeSetup.exe` (installs per user, no admin) and the app folder
`dist\windows\Gabe\Gabe.exe`. It can't be built on a Mac: the **Windows app** GitHub Actions
workflow builds it on every PR and push to `main`, and attaches `GabeSetup.exe` to a release for
`v*` tags. Add a `GOOGLE_OAUTH_CLIENT_JSON` repo secret to include Sign in with Google. Settings
live in `%APPDATA%\Gabe\`, the API key in Windows Credential Manager.

- The build quits a running Gabe and replaces `/Applications/Gabe.app`, so it survives the
  checkout being deleted. `INSTALL=0` skips that; `INSTALL_DIR=~/Applications` installs there.
- Settings and data live in `~/Library/Application Support/Gabe/` (`config/`, `data/`); the
  first launch copies a starter config there. `GABE_HOME` overrides the location.
- The Anthropic API key is entered in Settings → AI model and kept in the macOS Keychain.
- Gmail: Settings → Email → Choose file… (the Google OAuth client JSON), then Connect Gmail.
- Google Calendar: Settings → Calendar → Sign in with Google. Today shows the day's meetings.
- The daily brief is written on schedule while the app is open. If it was missed (the app
  was closed or crashed at brief time), it's written when the app next starts.
- Work cut off by a restart or crash is picked up again on start. Gabe gets the original
  request and the steps already done, so it doesn't repeat them. A job that keeps taking the
  app down is stopped after two retries. Approvals and questions left unanswered are asked again.
- Attach a task when you give a bot a job (the paperclip on an agent card or in a
  conversation), or use "Give to a bot" on a task. The bot gets the task's details and is
  asked to keep it updated, and the task shows which bot has it.
- Recurring jobs: ask Gabe ("check my email every half hour", "every Monday at 9, chase
  overdue tasks") and it sets one up. Each run happens in its own conversation with a fresh
  memory, under the same approvals as anything else, and stays quiet when there's nothing to
  report. They run while the app is open (a missed run happens once on the next start) and are
  listed on the Tasks screen, where you can run, pause or delete them.
- When Gabe or a bot asks you something (`ask_user`) or needs an approval, you get a macOS
  notification (click it to answer), a Dock badge with the count, and a banner in the app.
  The run waits for your answer. The first time, macOS asks to allow Gabe's notifications.
- After installing a new build, macOS may ask once to let Gabe read its key from the
  Keychain ("Always Allow"). The window opens regardless; Gabe can't reach the AI until then.
- Run `packaging/macos/make_signing_cert.sh` once: it creates a local "Gabe Local Signing"
  certificate that the build then uses, so every build has the same signature and macOS keeps
  Gabe's notification and Keychain permissions. Other Macs still need a Developer ID
  (`SIGN_ID`) and a notarized .dmg.

### Updates

Each copy of Gabe keeps itself up to date from this repository's `main` branch (Settings →
Updates, or `gabe update` in a checkout). It checks every hour and, by default, installs as soon
as nothing is running; "Ask first" waits for **Update now**.

- **Mac:** downloads `Gabe-mac.zip` from the `mac-latest` release, swaps it in where the app is
  and reopens it. No token or build tools needed.
- **Windows:** installs `GabeSetup.exe` from the newest successful **Windows app** build of
  `main`, silently, then reopens Gabe.
- **Checkout:** `git pull --ff-only` and `pip install -e .`; restart Gabe yourself.
- **Windows** needs a GitHub token (GitHub only serves workflow files to signed-in users): a
  fine-grained token with **Contents: read** and **Actions: read**, pasted into Settings →
  Updates (kept in the keychain). `GITHUB_TOKEN`/`GH_TOKEN` or a `gh auth login` also work.
- Builds record their commit in `gabe/_build.py`; that's what's compared with GitHub. A build
  that's ahead of `main` (a feature branch) isn't replaced. `updates:` in `gabe.yaml` sets
  `repo`, `branch`, `auto` and `check_every_hours`. The install log is
  `data/updates/update.log`.

Home is **Agents**: a live map of who hands work to whom, each agent's actions today on a
timeline, and controls per agent (give it a job, pause/resume, set what it may do, approve
its requests). "Request an agent" has Gabe propose a new specialist for a role; you edit and
approve it before it's added. Other screens: Today, Approvals, Tasks, Inbox, Notes, Activity,
Settings.

For UI work: `gabe web --no-open` and `cd web && npm run dev` (proxies `/api`), against the
repo's `config/gabe.yaml`. Settings edits that file in place and keeps the original as
`gabe.yaml.bak` (YAML comments are not preserved).

## How it adapts to a company

| Lever | Where | What it does |
|---|---|---|
| Company profile & facts | `company:` in `config/gabe.yaml` | Who's who, timezone, rhythms |
| Playbooks | `config/playbooks/*.md` | How *this* company does things (SOPs Gabe follows) |
| Integrations | `integrations:` | Which systems Gabe can touch; custom ones load by module path |
| Policy | `policy:` | What runs unattended, what needs a human, what's blocked |
| Team | `team_file:` (`config/team.yaml`) | A hierarchy of assistants under Gabe, each with a job, skills and freedom level. Edited on the Agents screen |
| Model | `provider:` | Anthropic API now; local OpenAI-compatible server (GLM) later |

### Writing an integration

Any importable module with a `setup(registry, ctx)` function:

```python
# acme_gabe/hubspot.py
from gabe.tools.registry import Risk, schema

def setup(registry, ctx):
    api_key = ctx.settings["api_key"]

    @registry.add("find_deal", "Look up a deal in HubSpot by name.",
                  schema({"name": {"type": "string"}}), Risk.READ, "hubspot")
    def find_deal(name: str):
        ...
```

```yaml
integrations:
  - name: acme_gabe.hubspot
    settings: {api_key: ...}
```

Tag every tool with a `Risk` (`read`, `write`, `external`). The policy gates on it.

### Switching to a local model

```yaml
provider:
  kind: openai_compatible
  base_url: http://gpu-box.local:8000/v1   # vLLM / SGLang serving GLM
  model: glm-5.3
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Built-in integrations

- `core`: current time in the company timezone
- `memory`: remember / recall / forget durable notes
- `tasks`: create, list and update tasks with owners and due dates
- `email`: Gmail / Google Workspace, one or more mailboxes (`settings.accounts`; add them in the web app) (`search_email`, `read_email`, `draft_email`,
  `send_email`, `mark_email_read`), or an `outbox` backend that saves outgoing mail to disk without delivering it. See [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md).
- `calendar`: Google Calendar, or a `local` practice calendar where nobody is invited
  (`list_events`, `find_free_time`, `block_time`, `create_event`, `update_event`,
  `cancel_event`, `respond_to_invite`). Anything guests see asks first by default; the daily
  brief includes today's meetings. See [docs/GMAIL_SETUP.md](docs/GMAIL_SETUP.md#google-calendar).
- `mcp`: any [MCP](https://modelcontextprotocol.io) server's tools (see below).
- `faq`: the FAQ bot. Upload FAQ documents (text, Markdown, Word, CSV, JSON) and Gabe answers
  customers' questions in Slack from them, in a thread, under your approval rules; what it
  can't answer goes to a team channel and/or the task list. Also gives Gabe a `search_faq`
  action. See [docs/FAQ_BOT.md](docs/FAQ_BOT.md).

## MCP servers

Plug in existing MCP servers (Notion, Linear, Slack...) with
`pip install -e '.[mcp]'` and an `mcp` entry under `integrations:`:

```yaml
  - name: mcp
    settings:
      call_timeout: 60                      # seconds per tool call
      connect_timeout: 30                   # seconds to start; raise it for a first `npx -y` download
      servers:
        notion:                             # stdio: Gabe starts the process
          command: npx
          args: ["-y", "@notionhq/notion-mcp-server"]
          env: {NOTION_TOKEN: "${NOTION_TOKEN}"}       # ${VAR} from the environment
          tools: {API-post-search: read}    # per-tool risk
        linear:                             # streamable HTTP
          url: https://mcp.linear.app/mcp
          headers: {Authorization: "Bearer ${LINEAR_TOKEN}"}
          include: [list_issues, create_issue]         # or exclude: [...]
```

- Tools are named `<server>__<tool>` and can be given to team members like any action.
- Risk is `external` (asks first by default) unless you set `risk:` for the server or
  `tools:` per tool. A server's own read-only hints are ignored unless
  `trust_annotations: true`; nothing else ever lowers risk.
- Policy works by server name: `policy: {integrations: {notion: allow, linear: ask}}`.
- Servers start once and stay connected; one that fails is skipped (its tools are simply
  missing) and retried a minute later. Status is in Settings → Connections; stdio servers'
  stderr goes to `data/mcp/logs/<server>.log`.
- Gabe.app launches stdio servers with a minimal PATH: use an absolute `command`, or set
  `PATH` in `env` (e.g. `/opt/homebrew/bin:/usr/bin:/bin` for `npx`).
- Remote servers take bearer headers only; OAuth sign-in isn't supported yet.

## Changing your Gabe with Claude Code

Everything that makes an installation yours lives in its config folder
(`~/Library/Application Support/Gabe/config/` for the app): settings, team, playbooks,
and your own integrations as Python files in `integrations/` (listed by name under
`integrations:`; a broken one is skipped, never fatal). Gabe rereads the folder on every
request, so edits apply at once. Gabe writes a `CLAUDE.md` there explaining the format to
Claude Code (your own notes go in `CLAUDE.local.md`).

- **Settings → Customize → Open in Claude Code** opens Terminal running `claude` in that
  folder. Ask for what you want ("connect our Linear", "add a bot that chases invoices").
- **Ask Gabe**: with the `claude_code` integration, Gabe has a `change_gabe` action that
  hands the request to Claude Code. Every request needs a person's OK. Claude Code runs
  restricted (no shell, only that folder); the folder is backed up to
  `data/claude_code/backups/` first and checked after; a change that fails the check gets
  one fix-up, then is undone. Not offered when Claude Code isn't installed.
- Claude Code runs on Gabe's own model setup: with `provider: anthropic`, Gabe's API key
  and model; with `provider: openai_compatible` (GLM on vLLM, LiteLLM...), the same server,
  key and model through its Anthropic-format API (`…/v1` → `…/v1/messages`), for every
  tier, with Anthropic telemetry off. Where that API lives elsewhere (Z.ai:
  `https://api.z.ai/api/anthropic`), set `base_url` and `auth_token: "${VAR}"` under
  `claude_code`. `model:` there overrides the model; `use_claude_login: true` uses your
  own Claude sign-in instead. With no Gabe API key set, it falls back to that sign-in.
- `gabe check` (in the app: `Gabe.app/Contents/MacOS/Gabe check`) checks the folder after
  any edit.

## Daily briefing

`gabe brief` runs a **read-only** Gabe. It can look at tasks, notes and email, but it
can't change or send anything. It writes the brief to the terminal and to
`data/briefings/YYYY-MM-DD.md`, using the structure in
`config/playbooks/daily-brief.md`. With the calendar on, the day's meetings are fetched
before the brief is written and always appear in it (any the writer leaves out are added
under "Today's calendar"). `gabe schedule` runs it at `briefing.time` on
`briefing.days`. Alternatively, call `gabe brief` from cron or launchd.

If `briefing.email_to` is set, the finished brief is also emailed through the connected
Gmail account, with plain text and formatted HTML versions. `me` means the connected
mailbox itself. Recipients are fixed in config, not chosen by the model, so this send
skips the approval prompt but is still audited. Use `gabe brief --no-email` to skip it.

On macOS, `scripts/install_launchd.sh` installs a LaunchAgent that runs `gabe brief`
at `briefing.time` on `briefing.days`, using the Mac's local timezone. A run missed
while the Mac was asleep fires when it wakes. Logs go to `data/logs/`. Re-run the
script after changing the schedule, or run `scripts/install_launchd.sh uninstall` to remove it.

## Gabe's team

Gabe can hand work to a team of AI assistants arranged like an org chart. Gabe is at
the top; each member reports to Gabe or to another member, and can hand work to its own
direct reports (up to 4 levels deep). The example team in `config/team.yaml`:

```
Gabe
├── Operations lead ── Scheduler (tasks and calendar)
├── Communications ─── Inbox triage
└── Notes keeper
```

With a team, Gabe is a dispatcher: it does very little itself and hands requests to the
member whose job fits. It keeps only `assistant.own_tools` (default: checking the time and
looking up notes) so it can brief people well, plus any action that no active team member
can do. With no team, Gabe does everything itself. The daily brief run is always hands-on.

The **Agents** screen in the app is the org chart: change anyone's name, job, instructions,
manager, skills and freedom level, pause or remove them, give them a job directly, or ask
Gabe to propose a new member. Changes save to `config/team.yaml`.

**Bots can add bots.** Gabe has an `add_team_member` action: when work keeps coming up that
nobody fits, it proposes a new member under itself or under anyone on the team. Any member
given that action (in its skills) can add members within its own branch. A person approves
every new member first (the approval card shows why, the name, job, manager, freedom, actions
and first task, and can be edited), unless `policy.tools.add_team_member` says otherwise. A bot
can only give a new member actions it has itself and at most its own freedom (never "trusted"),
and a "look only" bot can't add anyone. New members are for lasting roles, not single tasks:
Gabe must say where it belongs (`reports_to`), a member that overlaps an existing one's area
must go under that member, and every new member starts on a `first_task` straight away. Gabe
either fixes a new member's model or leaves it on Default, where it picks a model each time it
delegates: Haiku for most work, Sonnet where judgement or careful writing matters, Opus only for
high-priority, difficult work (Haiku if it doesn't say). A bot can't give a job or a new member a
stronger model than its own. Bots that have never done anything show as "Never used" on the Agents screen.

**Nothing fails silently.** Every job ends in a notice: done or failed, as a banner in the app
and a system notification in the desktop app (click it to open the conversation). A bot that
declines, gets cut off, runs out of steps, or reports it couldn't do the job (its last line is
`JOB FAILED: <reason>`) has failed, not finished. A failed hand-off goes back
to its manager as an error to deal with. When a job fails, Gabe gets it in a new "Fixing: …"
conversation, works out why and retries, or asks you when only a person can fix it. That
includes the daily brief and inbox sorting. A failed fix isn't handed back again, and there's no
fix attempt when Gabe can't reach its own model (API key, connection, rate limit).

Controls, and how they combine:

| Control | Effect |
|---|---|
| Working / Paused | A paused member, and everyone under them, gets no work. |
| Skills | The tools a member may use. Skills from a connection that isn't set up are skipped and flagged on the page. |
| Freedom level | `look_only` (can't change or send anything), `ask_first` (asks before any change), `standard` (asks before contacting anyone outside), `trusted` (company rules only). It can only make a member **more** careful than `policy:`, never less. |
| Model | `haiku`, `sonnet` or `opus`, mapped to model ids by `provider.tiers` (Claude 5.5 models by default). Left out (Default), its manager picks one for each job. |
| Reports to | Only a member's manager can hand it work. Loops and chains deeper than 4 are refused. |

Older configs with `delegates:` still work: every delegate reports straight to Gabe.
The first edit on the Agents screen moves them into `team.yaml`.

## Planned

- Web UI: [docs/UI_PLAN.md](docs/UI_PLAN.md)
- Slack integration (talking to Gabe, approvals in Slack): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap).
  The customer FAQ bot is done: [docs/FAQ_BOT.md](docs/FAQ_BOT.md).

Every tool call is appended to `data/audit.jsonl`.

## Usage and cost

Every model call is appended to `data/usage.jsonl` with the user, the kind of task
(`chat`, `brief`), the agent or delegate, the model that answered, and its input,
cached-input and output tokens. The user is `--user`, else `$GABE_USER`, else your login.

```bash
gabe usage                          # by user, task and model, all time
gabe usage --since 2026-11-01 --by user
gabe usage --by model,agent
```

Estimated cost uses the `pricing:` table in `config/gabe.yaml` ($ per million tokens).
Models missing from it are counted but not priced, and the report says so.
