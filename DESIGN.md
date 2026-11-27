# Design

Gabe web app (`web/`). Operate-mode product UI for non-technical admin staff.

## Principle

A ledger, colour-coded. Pages are white and ruled like an operations log; each area of Gabe
owns one hue and uses it everywhere it appears: nav tile, page header tile, the rule under each
section head, row tiles, chips, activity dots and the page's primary button. Ink and hairlines
carry everything else. (Chosen from the mockups in `docs/mockups/ui-refresh.html`, option A2.)

## Colour

Tokens live in `web/src/styles.css` (`:root`, dark overrides under
`prefers-color-scheme` and `[data-theme="dark"]`).

| Area | Fill | Used for |
|---|---|---|
| Today | `--today` #ffc81a | home, daily brief |
| Ask Gabe | `--ask` #4d8dff | chat, focus ring, links |
| Approvals | `--approvals` #ff4fa3 | anything waiting for a person |
| Tasks | `--tasks` #2fd36b | tasks; also "done" / approve |
| Inbox | `--inbox` #16c7e8 | email |
| Notes | `--notes` #a983ff | memory |
| Activity | `--activity` #ff8a2b | audit log |
| Settings | `--settings` #b6e43a | settings |
| Agents | `--ink` (fill), `--bg` (text) | agents; ink, not a hue, because agents work across every area |
| Danger | `--danger` #ff5a4e | overdue, blocked, failed |

- Fills always carry `--on-fill` (near-black) text.
- Each hue has an `-ink` variant for text on grey (darker in light theme, the fill itself in dark).
- `data-area="<area>"` on any element sets `--hue`, `--hue-ink`, `--hue-soft`; components read those.
- Base: light theme is white throughout (`--bg` #ffffff, ink #1b2533, hairlines #e4e7eb) with a
  white rail split off by a 1px rule; dark theme is near-black (#15181d) with the same structure.
  Theme follows the system; the Light / Auto / Dark switch sits in the Settings page header.

Agents have colours too: Gabe blue, the briefing writer yellow, each specialist the colour of
the work it mostly does (tasks green, email cyan…), assigned in config order so existing
agents keep their colour when one is added. A specialist whose kind of work already has an agent
is neutral grey; it never borrows an unrelated area's hue.

## Agents view

`/agents`, in the last nav group. Top to bottom: one "give a job" bar (agent picker, task attach,
text, Send; "Write brief now" when the briefing writer is picked), a map (You → Gabe →
specialists) where an edge animates while work is being handed along it, a Team list (one ruled
row per agent: name, what it's doing or its job, manager, done today, state; the row opens the
agent's sheet), and a timeline lane per agent with one dot per action (coloured by action type;
hollow red ring when it was stopped). Under 900px the map is hidden and the Team list carries it.

The agent sheet holds every per-agent control: pending approvals, settings, freedom (linked to the
company approval rules), what it can do as checkboxes grouped by kind of work, recent jobs, and
Pause / Remove in the footer.

Settings stay visible without opening anything: every map node and Team row shows model · freedom
and one dot per kind of work it can do (area colours), and every row has a Pause / Resume button.
Hovering a map node shows a settings card beside it (pause, model, freedom, what it can do, "All
settings"); a click keeps it open until a click elsewhere or Escape. Changes there save at once
with a toast; the sheet is for name, job, instructions, manager and the tool checkboxes.

The FAQ bot is listed as the last Team row and has its own page at `/agents/faq`.

### Settings

Six tabs: Company · Daily brief · Approval rules · Connections (Email, Calendar, other services) ·
Playbooks · Advanced (API key and usage, Claude Code, Updates). Old tab addresses redirect.

## Type

IBM Plex Sans Variable for everything (self-hosted via `@fontsource-variable/ibm-plex-sans`);
IBM Plex Mono (`--mono`) only for times, counts, dates and code. Page titles 28/600 over a 2px
ink rule; section heads 13/600 uppercase, tracked 0.05em, over a 2px rule in the area hue; body
14.5/1.5; meta 13. Tabular numerals everywhere. Six sizes only, as tokens: `--text-xs` 12,
`--text-sm` 13, `--text-md` 14.5, `--text-lg` 17, `--text-xl` 22, `--text-2xl` 28.

## Components

Panels are open, ruled sections (no box): a head underlined in its hue, rows below separated by
hairlines, flush with the head. Objects you act on share one bordered card rule (`.card`; raised fill, 1px line,
`--radius` 8px, no shadow): approvals (pink 2px outline while waiting), letters, board cards,
notes (tinted lilac), the agent map, mailbox lists. Chips are small `--radius-sm` labels, soft or
solid. Floating surfaces (menus, the agent settings card, tiles) use `--radius-lg` 12px. Three
radii only: 5 / 8 / 12. Segmented controls, side sheets for create/edit (no modals),
toasts bottom-right. Buttons: `--radius-sm`, default outlined, `btn-primary` in the area hue,
`btn-go` green for approve. Spacing comes from a 4 / 8 / 12 / 16 / 24 scale; one-off spacing
uses the utility classes at the end of `styles.css`, not inline styles. Inline styles are only for
values computed at runtime (map positions, timeline offsets).

Today is the home screen (`/`). "Needs you" shows up to three waiting approvals and questions
inline, decided in place; the rest are summary rows with a hue tile. Its count is the approvals
waiting, shown only when there are some. The brief's date appears once, in its picker; the
brief's own title line is not rendered.

## Copy

Terse. Labels and buttons carry meaning; no explanatory subtitles or helper paragraphs.
Tool names, IDs and config keys are always translated (`web/src/lib/tools.ts`).

## Layout

Rail (232px) + content (max 1240px). Nav groups: Today, Ask Gabe, Approvals, Tasks · Inbox,
Notes, Activity · Agents, Settings. Under 900px the rail becomes a bottom tab bar
(Today, Ask, Approve, Tasks, More).
