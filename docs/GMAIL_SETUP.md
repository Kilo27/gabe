# Connecting Gabe to Gmail and Google Calendar

Gabe uses the Gmail API with OAuth. You create an OAuth client once in Google Cloud.
Then you sign in once from the terminal, and Gabe stores a refresh token locally.

## 1. Create the OAuth client (one-time, ~5 minutes)

1. Go to <https://console.cloud.google.com/> and create or select a project.
2. **APIs & Services → Library**: enable the **Gmail API** and the **Google Calendar API**.
3. **APIs & Services → OAuth consent screen**:
   - For Google Workspace, choose **Internal** so only your organisation can use it.
     Otherwise choose External and add yourself as a test user.
   - Add the scopes `https://www.googleapis.com/auth/gmail.modify`,
     `https://www.googleapis.com/auth/calendar.events` and
     `https://www.googleapis.com/auth/calendar.freebusy`.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
   Choose application type **Desktop app**.
5. Download the JSON and save it as `config/gmail_client_secret.json`. Git ignores it.

## 2. Authorise Gabe

```bash
pip install -e '.[gmail]'
gabe auth gmail
```

A browser window opens. Sign in with the mailbox Gabe should use and approve the
request. The token is saved to `data/email/gmail_token.json` (gitignored, mode 600).

Or use **Settings → Email → Connect Gmail** in the web app. **Add mailbox** there connects
more mailboxes (sign in with a different Google account). They're listed under
`settings.accounts`; the first is the main one (the brief's "me"). With more than one, every
email action takes an `account` and searches cover all mailboxes.

## What Gabe can do with it

| Tool | Risk | Default policy |
|---|---|---|
| `search_email`, `read_email` | read | runs automatically |
| `draft_email`, `mark_email_read` | write | runs automatically |
| `send_email` | external | asks a person first |

The `gmail.modify` scope allows reading, labelling, drafting and sending. It does not
allow permanent deletion. Change what runs unattended in `policy:` in `config/gabe.yaml`.
For example, `tools: {send_email: deny}` makes Gabe draft only.

Gabe's system prompt treats email content as information from the sender, never as
instructions. Sending always goes through the approval policy.

## Google Calendar

The calendar uses the same OAuth client, with its own sign-in and token
(`data/calendar/google_token.json`), so connecting or disconnecting one never affects the other.

```bash
gabe auth calendar
```

Or **Settings → Calendar → Sign in with Google** in the app. **Practice** mode
(`backend: local`) keeps a calendar in `data/calendar/events.json` instead: nobody is invited.

| Tool | Risk | Default policy |
|---|---|---|
| `list_events`, `find_free_time` | read | runs automatically |
| `block_time` (own calendar, no guests) | write | runs automatically |
| `create_event`, `update_event`, `cancel_event`, `respond_to_invite` | external | asks a person first |

`find_free_time` checks colleagues' calendars through free/busy (times only, no details) where
their calendars are shared with the signed-in account, and keeps to `working_hours` /
`working_days` (calendar settings, else `company.working_hours`, else Mon-Fri 09:00-17:30).
Times are in `company.timezone`. `calendar_id` picks a calendar other than the primary one.
Event titles and descriptions are treated as information, never as instructions.
