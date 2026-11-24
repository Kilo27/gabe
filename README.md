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
gabe ui                             # open the Team page in your browser
gabe --unattended -m "..."          # refuses anything that needs approval
pytest                              # tests use a scripted provider, no API calls
```

## How it adapts to a company

| Lever | Where | What it does |
|---|---|---|
| Company profile & facts | `company:` in `config/gabe.yaml` | Who's who, timezone, rhythms |
| Playbooks | `config/playbooks/*.md` | How *this* company does things (SOPs Gabe follows) |
| Integrations | `integrations:` | Which systems Gabe can touch; custom ones load by module path |
| Policy | `policy:` | What runs unattended, what needs a human, what's blocked |
| Team | `team_file:` (`config/team.yaml`) | A hierarchy of assistants under Gabe, each with a job, skills and freedom level. Edited from `gabe ui` |
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
- `email`: Gmail / Google Workspace (`search_email`, `read_email`, `draft_email`,
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
└── Communications ─── Inbox triage
```

`gabe ui` opens a local page (http://127.0.0.1:8765, this machine only) written for
people who don't read code:

- **Team**: the org chart. Click anyone to see a plain-English summary ("reports to
  Gabe, can do 3 things on their own and will ask you before 1"), and change their
  name, job, instructions, manager, skills and freedom level. You can also pause or
  remove them. Changes save to `config/team.yaml`.
- **Ask Gabe**: send a request and watch each hand-off live ("Gabe asked Operations
  lead to help", "Scheduler created a task..."). Actions that need a person show up as
  cards with **Approve** / **Decline**, and the run waits until you answer.
- **What happened**: the audit log in plain English, with the chain of who asked whom.

Controls, and how they combine:

| Control | Effect |
|---|---|
| Working / Paused | A paused member, and everyone under them, gets no work. |
| Skills | The tools a member may use. Skills from a connection that isn't set up are skipped and flagged on the page. |
| Freedom level | `look_only` (can't change or send anything), `ask_first` (asks before any change), `standard` (asks before contacting anyone outside), `trusted` (company rules only). It can only make a member **more** careful than `policy:`, never less. |
| Reports to | Only a member's manager can hand it work. Loops and chains deeper than 4 are refused. |

Older configs with `delegates:` still work: every delegate reports straight to Gabe.
Move them into a team file to edit them from the page.

## Planned

- Web UI: [docs/UI_PLAN.md](docs/UI_PLAN.md)
- Slack integration: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap)

Every tool call is appended to `data/audit.jsonl`.
