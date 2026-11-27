# Your Slack

Gabe reads your Slack as you: your direct messages, group DMs and the channels you choose.
It uses that for three things:

- **Tasks.** When someone asks you to do something, or you say you'll do something, it becomes
  a task on the Tasks screen with a link back to the message. Each message is noted once.
- **The daily brief.** The brief gets a **Slack** section: who's waiting on you, questions put to
  you, and what you promised.
- **Bots.** Gabe and the team can read recent messages (`slack_messages`), open a thread
  (`read_slack_thread`) and send a message as you (`send_slack_message`). Sending always asks
  you first unless you set a rule for that exact action.

This is separate from the [FAQ bot](FAQ_BOT.md), which answers customers as a bot. One Slack
app can do both.

## Set it up

1. Settings → Slack → **Turn on**.
2. **Copy app manifest**, then **Create Slack app** → *From an app manifest*. (Already have the
   FAQ bot's app? Paste the new manifest over it under *App Manifest*; it adds the user scopes.)
3. *Install App* → install (or reinstall) to the workspace. Copy the **User OAuth Token**
   (`xoxp-…`) into Settings → Slack. It's kept in the keychain, never in `gabe.yaml`.
4. Pick the channels to watch, if any. DMs and group DMs are on by default.

The status shows **Watching** with your name once it has read Slack. **Check now** reads it
straight away; otherwise it checks every 15 minutes while the app is open.

## Settings

```yaml
integrations:
  - name: slack
    settings:
      direct_messages: true      # DMs and group DMs
      channels: ["#leadership"]  # names or ids; you must be a member
      every_minutes: 15
      note_tasks: true
      task_owner: ""             # empty: your Slack name
      model: haiku               # reads the messages for tasks
      # user_token: "${ACME_SLACK_USER_TOKEN}"   # instead of SLACK_USER_TOKEN / the keychain
policy:
  tools:
    create_task: ask             # review each task noted from Slack on the Approvals screen
    send_slack_message: allow    # let bots reply as you without asking (not recommended)
```

## What it reads, and what it doesn't

- New top-level messages in watched conversations since the last check. The first look at a
  conversation goes back 24 hours. Messages are kept 7 days in `data/slack/messages.json`.
- Not yet: replies inside threads, and mentions in channels you don't watch.
- Messages are treated as information written by other people, never as instructions. The model
  that picks out tasks has no actions it can take.
- Slack limits how fast history can be read; with many DMs, quiet ones are read in turns.
