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

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,gmail]'
export ANTHROPIC_API_KEY=...        # or: ant auth login
gabe                                # interactive, uses config/gabe.yaml
gabe -m "Give me the weekly brief"  # one-shot
gabe brief                          # today's daily briefing (saved to data/briefings/)
gabe schedule                       # run the briefing every weekday at the configured time
gabe auth gmail                     # connect Gmail (see docs/GMAIL_SETUP.md)
gabe --unattended -m "..."          # refuses anything that needs approval
pytest                              # tests use a scripted provider, no API calls
```

## Desktop app (macOS; Windows next)

Gabe ships as a desktop app, not a website. It runs its own local service inside a native
window; that service only accepts calls from its own window (a fresh token each launch).

```bash
pip install -e '.[desktop,gmail,build]'
packaging/macos/build.sh        # → dist/macos/Gabe.app and Gabe.dmg (needs Node, ImageMagick)
gabe app                        # run it from source instead
```

- Settings and data live in `~/Library/Application Support/Gabe/` (`config/`, `data/`); the
  first launch copies a starter config there. `GABE_HOME` overrides the location.
- The Anthropic API key is entered in Settings → AI model and kept in the macOS Keychain.
- Gmail: Settings → Email → Choose file… (the Google OAuth client JSON), then Connect Gmail.
- The daily brief is written on schedule while the app is open. If it was missed (the app
  was closed or crashed at brief time), it's written when the app next starts.
- Work cut off by a restart or crash is picked up again on start. Gabe gets the original
  request and the steps already done, so it doesn't repeat them. A job that keeps taking the
  app down is stopped after two retries. Approvals and questions left unanswered are asked again.
- Attach a task when you give a bot a job (the paperclip on an agent card or in a
  conversation), or use "Give to a bot" on a task. The bot gets the task's details and is
  asked to keep it updated, and the task shows which bot has it.
- When Gabe or a bot asks you something (`ask_user`) or needs an approval, you get a macOS
  notification (click it to answer), a Dock badge with the count, and a banner in the app.
  The run waits for your answer. The first time, macOS asks to allow Gabe's notifications.
- After installing a new build, macOS may ask once to let Gabe read its key from the
  Keychain ("Always Allow"). The window opens regardless; Gabe can't reach the AI until then.
- Run `packaging/macos/make_signing_cert.sh` once: it creates a local "Gabe Local Signing"
  certificate that the build then uses, so every build has the same signature and macOS keeps
  Gabe's notification and Keychain permissions. Other Macs still need a Developer ID
  (`SIGN_ID`) and a notarized .dmg.

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

## Daily briefing

`gabe brief` runs a **read-only** Gabe. It can look at tasks, notes and email, but it
can't change or send anything. It writes the brief to the terminal and to
`data/briefings/YYYY-MM-DD.md`, using the structure in
`config/playbooks/daily-brief.md`. `gabe schedule` runs it at `briefing.time` on
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
├── Operations lead ── Scheduler
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
must go under that member, and every new member starts on a `first_task` straight away. Bots
that have never done anything show as "Never used" on the Agents screen.

Controls, and how they combine:

| Control | Effect |
|---|---|
| Working / Paused | A paused member, and everyone under them, gets no work. |
| Skills | The tools a member may use. Skills from a connection that isn't set up are skipped and flagged on the page. |
| Freedom level | `look_only` (can't change or send anything), `ask_first` (asks before any change), `standard` (asks before contacting anyone outside), `trusted` (company rules only). It can only make a member **more** careful than `policy:`, never less. |
| Reports to | Only a member's manager can hand it work. Loops and chains deeper than 4 are refused. |

Older configs with `delegates:` still work: every delegate reports straight to Gabe.
The first edit on the Agents screen moves them into `team.yaml`.

## Planned

- Web UI: [docs/UI_PLAN.md](docs/UI_PLAN.md)
- Slack integration: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap)

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
