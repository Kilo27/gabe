# Gabe's config folder

<!-- Written by Gabe and replaced when Gabe updates. Put your own notes in CLAUDE.local.md. -->

This folder is one company's installation of Gabe, an AI chief of staff. Gabe's code is
not here (it is in the app); everything that makes this installation different is. The
person you are working with wants to change how their Gabe works. Change only these files.

Gabe rereads this folder on every request, so edits take effect at once: no restart.

## After every change

Run the check and fix anything it reports before you finish:

```
{{CHECK_COMMAND}}
```

`Problem:` lines mean Gabe won't work as intended. `Warning:` lines are worth a mention.

## Files

- `gabe.yaml`: the main settings (below).
- `team.yaml`: Gabe's team of AI assistants (below).
- `playbooks/*.md`: standing instructions Gabe follows, one Markdown file per routine
  (e.g. `daily-brief.md`). The file name is the playbook's name. Plain prose.
- `integrations/*.py`: the company's own integrations (below).
- `*.bak`: the app's backup of the previous version of a file it edited. Leave them.

The app's Settings screens also write `gabe.yaml` and drop YAML comments when they do,
so don't rely on comments to carry meaning.

## gabe.yaml

```yaml
assistant:
  name: Gabe
  own_tools: [get_current_time, recall]   # with a team, Gabe uses only these itself
company:
  name: Example Co
  timezone: Europe/Dublin                 # IANA name
  profile: |                              # who's who, roles, regular meetings
    ...
  working_hours: "Mon-Fri 09:00-17:30"    # any other key: a fact Gabe is told
provider: {kind: anthropic, model: claude-opus-5-5, effort: high}
data_dir: ../data                         # never move this
playbooks_dir: playbooks
integrations:                             # what Gabe can do (below)
  - core
  - memory
  - tasks
  - name: email
    settings: {backend: gmail}            # gmail | outbox (practice: nothing is sent)
  - name: calendar
    settings: {backend: google}           # google | local (practice: nobody is invited)
policy:                                   # what may run without a person's OK
  defaults: {read: allow, write: allow, external: ask}
  tools: {send_email: ask}                # one action, by name
  integrations: {notion: ask}             # every action of one integration or MCP server
briefing: {time: "08:00", days: [mon, tue, wed, thu, fri], email_to: [me]}
team_file: team.yaml
```

Decisions are `allow`, `ask` or `deny`. Risks: `read` (looks things up), `write` (changes
Gabe's own tasks, notes, drafts), `external` (anything other people will see). Be careful
loosening rules: say plainly what a change lets Gabe do without asking.

## Integrations

Built in: `core` (time), `memory` (notes), `tasks`, `email`, `calendar`, `mcp`, `claude_code`.

`calendar` actions: `list_events`, `find_free_time` (read), `block_time` (write: own calendar,
no guests), `create_event`, `update_event`, `cancel_event`, `respond_to_invite` (external:
guests see them). Settings: `backend`, `calendar_id`, `working_hours`, `working_days`.

**MCP servers** connect existing tools (Notion, Linear, Slack...):

```yaml
  - name: mcp
    settings:
      call_timeout: 60
      connect_timeout: 30            # raise for a first `npx -y` download
      servers:
        notion:                      # stdio
          command: npx
          args: ["-y", "@notionhq/notion-mcp-server"]
          env: {PATH: "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin", NOTION_TOKEN: "${NOTION_TOKEN}"}
          # risk: external           # this server's default
          # tools: {API-post-search: read}   # per action
          # include: [...] / exclude: [...]
          # trust_annotations: true  # the server's read-only hints count
        linear:                      # remote, streamable HTTP
          url: https://mcp.linear.app/mcp
          headers: {Authorization: "Bearer ${LINEAR_TOKEN}"}
```

Actions are named `<server>__<tool>`. The app starts servers with a minimal PATH, so set
PATH in `env` (or use an absolute `command`). Never write a token into these files: use
`${VAR}` and tell the person to add the variable to `../.env` (`NAME=value` lines).

**Your own integration** is a Python file `integrations/<name>.py` listed as `- <name>`
(or `{name: <name>, settings: {...}}`) under `integrations:`. Don't reuse a built-in name.
It can use the standard library, `httpx` and `yaml`; nothing else is installed.

```python
from gabe.tools.registry import Risk, schema


def setup(registry, ctx):
    base = ctx.settings.get("base_url", "https://example.com")   # from gabe.yaml
    # ctx.path("x.json") is a file under data/<name>/ for this integration's own storage.

    @registry.add(
        "list_invoices",                                  # unique; letters, digits, _
        "List unpaid invoices, newest first.",            # tell the model when to use it
        schema({"customer": {"type": "string", "description": "Customer name"}}, required=[]),
        Risk.READ,                                        # READ | WRITE | EXTERNAL
        "invoices",                                       # integration name, for policy
    )
    def list_invoices(customer: str = "") -> str:
        import httpx
        return httpx.get(f"{base}/invoices", params={"customer": customer}, timeout=30).text
```

Pick the risk honestly: anything that sends, posts, pays or changes another system is
`EXTERNAL`. A broken file is skipped (the check shows why), never fatal.

## team.yaml

```yaml
members:
- id: invoices                 # lowercase letters, digits, _ ; "chief" is Gabe
  name: Invoice chaser
  job: One line: the ongoing work it owns (its manager picks by this).
  instructions: |-
    How it does the job, in plain sentences it can follow alone.
  reports_to: chief            # Gabe, or another member's id (at most 4 levels)
  skills: [list_invoices, draft_email]   # actions it may use; omit for all
  freedom: standard            # look_only | ask_first | standard | trusted
  active: true
```

Freedom can only make a member more careful than `policy`, never less. A member with
reports can hand them work; Gabe hands work to members that report to `chief`.

## Keep in mind

- Make the change asked for, nothing more. Keep existing settings, members and playbooks.
- Never put secrets in these files, and never edit `../data/`.
- If the request is unclear or risky (loosening approvals, deleting members), say so
  instead of guessing.
