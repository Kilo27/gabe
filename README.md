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
- The daily brief is written on schedule while the app is open.
- The build is ad-hoc signed, so other Macs show a Gatekeeper warning. For distribution set
  `SIGN_ID` to a Developer ID certificate and notarize the .dmg.

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
| Delegates | `delegates:` | Specialist sub-agents with their own instructions and tool subsets |
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

## Planned

- Web UI: [docs/UI_PLAN.md](docs/UI_PLAN.md)
- Slack integration: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#roadmap)

Every tool call is appended to `data/audit.jsonl`.
