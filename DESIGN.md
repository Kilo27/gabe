# Design

Gabe web app (`web/`). Operate-mode product UI for non-technical admin staff.

## Principle

Colour is the map. Each area of Gabe owns one hue and uses it everywhere it appears:
nav tile, page header tile, panel swatch, chips, activity dots and the page's primary
button. Grey carries everything else.

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
| Danger | `--danger` #ff5a4e | overdue, blocked, failed |

- Fills always carry `--on-fill` (near-black) text.
- Each hue has an `-ink` variant for text on grey (darker in light theme, the fill itself in dark).
- `data-area="<area>"` on any element sets `--hue`, `--hue-ink`, `--hue-soft`; components read those.
- Base: light theme is light grey content (`--bg` #e4e5e8, panels #f2f2f4) beside a dark grey
  rail (#1e1f22); dark theme is dark grey throughout. Theme follows the system; Light / Auto /
  Dark switch in the rail.

Agents have colours too: Gabe blue, the briefing writer yellow, each specialist the colour of
the work it mostly does (tasks green, email cyan…), assigned in config order so existing
agents keep their colour when one is added.

## Agents view

Home screen. A map (You → Gabe → specialists) where an edge animates while work is being
handed along it, cards with live status and controls, and a timeline lane per agent with one
dot per action (coloured by action type; hollow red ring when it was stopped).

## Type

Figtree Variable (self-hosted via `@fontsource-variable`), one family. Page titles 30/800,
panel titles 16.5/750, body 15/1.5, meta 13.5. Tabular numerals everywhere.

## Components

Panels (14px radius, 1px line, no nesting), chips (pill, soft or solid), segmented
controls, side sheets for create/edit (no modals), toasts bottom-right. Buttons: default
outlined, `btn-primary` in the area hue, `btn-go` green for approve.

## Copy

Terse. Labels and buttons carry meaning; no explanatory subtitles or helper paragraphs.
Tool names, IDs and config keys are always translated (`web/src/lib/tools.ts`).

## Layout

Rail (248px) + content (max 1240px). Under 900px the rail becomes a bottom tab bar
(Today, Ask, Approve, Tasks, More).
