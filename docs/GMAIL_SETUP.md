# Connecting Gabe to Gmail / Google Workspace

Gabe uses the Gmail API with OAuth. You create an OAuth client once in Google Cloud.
Then you sign in once from the terminal, and Gabe stores a refresh token locally.

## 1. Create the OAuth client (one-time, ~5 minutes)

1. Go to <https://console.cloud.google.com/> and create or select a project.
2. **APIs & Services → Library**: enable the **Gmail API**.
3. **APIs & Services → OAuth consent screen**:
   - For Google Workspace, choose **Internal** so only your organisation can use it.
     Otherwise choose External and add yourself as a test user.
   - Add the scope `https://www.googleapis.com/auth/gmail.modify`.
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
