# FAQ bot (Slack)

Gabe reads the questions customers post in Slack and answers the ones your FAQ covers,
in a thread under the question. It answers only from the FAQ documents you upload: no
guessing. What it can't answer goes to your team.

## Set it up

1. **Turn it on**: Settings → FAQ bot → Turn on.
2. **Upload your FAQ**: one or more documents. Readable formats:

   | Format | Layout |
   |---|---|
   | `.txt`, `.md` | `Q:` / `A:` lines; or a heading per question with the answer under it; or question lines ending in `?` with the answer below. `# Billing`-style headings with no text under them become sections. Anything else is split into paragraphs and still searched. |
   | `.docx` | Same, with Word headings counted as headings. |
   | `.csv`, `.tsv` | `question` and `answer` columns (`category` optional), or the first two columns. |
   | `.json` | `[{"question": "...", "answer": "..."}]` |

   Open a document on the screen to check how it was read. Use **Try it** to see what Gabe
   would say to a question (nothing is posted). Uploading a file with the same name replaces it.
   The files live in the config folder's `faq/`, so you can also drop them in there or edit
   them with Claude Code.
3. **Create the Slack app**: **Copy app manifest**, then **Create Slack app** → *From an app
   manifest*, pick your workspace and paste it. Then:
   - *Basic Information → App-Level Tokens*: generate one with the `connections:write` scope.
     That's the **app token** (`xapp-…`).
   - *Install App*: install to the workspace. That gives the **bot token** (`xoxb-…`).
4. Paste both tokens into Settings → FAQ bot → Slack and switch the bot **On**. The status
   shows **Connected** with your workspace name. Tokens are kept in the keychain, never in
   `gabe.yaml`.
5. **Invite the bot** to the channels customers ask in (`/invite @Gabe`). Customers can also
   message it directly.

The bot connects with Socket Mode (an outgoing connection), so no public URL or firewall
change is needed. It runs while the Gabe app is open; on a server, run `gabe slack`.

## Approval first

Each answer is the **Answer a customer on Slack** action (`reply_on_slack`). Like every action
that reaches outside the company, it asks first by default: drafts wait on the Approvals screen,
where you can approve, edit then approve, or say no. Once you trust it, set that action to
**Allow** under Settings → Approval rules and answers go straight out. **Deny** stops all answers.
Every answer is in the Activity log.

## Who it answers

- **Everyone but staff**: anyone, except the Slack member IDs listed under Staff.
- **Guests and other workspaces**: only guests and people from other workspaces (Slack Connect),
  so your own team can chat in the channel without the bot joining in.
- **Only these channels**: limit it to some of the channels it's in (`#help` or a channel ID).
- **Follow-ups in its threads**: it answers follow-up questions in threads it has answered,
  until someone else (a person on the team) replies there. Then it steps back.
- It skips greetings, thanks and chatter, other bots and edited messages, and answers anyone at
  most *Replies per person per hour* times.
- Mentioning it (`@Gabe …`) gets its attention anywhere, including in other people's threads.

## When the FAQ has no answer

Any combination of:

- **Tell this channel**: posts the question, who asked and a link in a team channel.
- **Add a task**: a task on the Tasks screen, for the owner you choose.
- **Reply to the customer**: a fixed message such as "Thanks, someone from the team will reply here."

The **Questions** list shows every question the bot handled and what happened. Filter by
**No answer** to see what's missing from your FAQ.

## Customizing it

Settings in `gabe.yaml` (all editable in the app):

```yaml
integrations:
  - name: faq
    settings:
      folder: faq                  # relative to gabe.yaml
      model: haiku                 # haiku | sonnet | opus
      tone: Friendly and brief.
      retriever: keyword           # or "your_package.module:ClassName"
      max_matches: 6               # entries shown to the model when the FAQ is large
      full_context_chars: 40000    # an FAQ up to this size is given to the model whole
      slack:
        enabled: true
        channels: []
        direct_messages: true
        answer: everyone           # everyone | external
        staff: [U0123ABC]
        follow_ups: true
        per_user_per_hour: 10
        unanswered:
          notify_channel: "#support-team"
          create_task: true
          task_owner: Support
          reply: ""
        # bot_token: "${ACME_SLACK_BOT_TOKEN}"   # instead of SLACK_BOT_TOKEN / the keychain
        # app_token: "${ACME_SLACK_APP_TOKEN}"
policy:
  tools: {reply_on_slack: allow}  # post answers without asking
```

**How answering works.** A small FAQ (up to `full_context_chars`) goes to the model whole, in
a cached system prompt, so paraphrased questions still find their answer cheaply. A larger
one is searched first (keyword search, no extra installs) and only the best `max_matches`
entries go to the model; a question that matches nothing costs no model call. The model
answers only from those entries, or says the question isn't covered. Output it can't read
is never posted.

**Your own search.** For a very large knowledge base, or to search your help centre, point
`retriever` at a class of your own:

```python
# acme_gabe/search.py
class HelpCentreSearch:
    def __init__(self, settings: dict): ...
    def search(self, query, entries, limit):  # -> [(entry, score), ...] best first
        ...
```

**Gabe and the team use it too.** The `search_faq` action lets Gabe and any bot you give it
to look things up in the FAQ, for example when replying to a customer email.

**Other channels.** The answering part (`gabe/faq/answer.py`) knows nothing about Slack, so
an email or web-chat front end can reuse it; `gabe/faq/slack.py` is the Slack one.

## Running it without the app

```bash
pip install -e '.[slack]'
export SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... ANTHROPIC_API_KEY=...
gabe slack
```

Approvals need the app, so with `gabe slack` set `reply_on_slack: allow` (it says so if you
haven't). Run one `gabe slack` per Slack workspace, each with its own config folder.
