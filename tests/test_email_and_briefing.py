import base64
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import gabe.integrations.gmail_backend as gb
from gabe.briefing import build_briefer, next_run, run_briefing
from gabe.config import load_config
from gabe.integrations import load_integrations
from gabe.tools.registry import ToolRegistry
from tests.conftest import ScriptedProvider
from tests.test_agent import done, results, tool_step


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class Call:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class FakeGmail:
    """Just enough of the googleapiclient surface for GmailClient."""

    def __init__(self):
        self.sent, self.drafted, self.modified = [], [], []
        self.msg = {
            "id": "m1", "threadId": "t1", "labelIds": ["UNREAD", "INBOX"], "snippet": "Can we move &amp; meet?",
            "payload": {
                "mimeType": "multipart/alternative",
                "headers": [{"name": "From", "value": "Alex <alex@x.co>"}, {"name": "Subject", "value": "Budget"},
                            {"name": "Message-ID", "value": "<abc@x.co>"}],
                "parts": [
                    {"mimeType": "text/html", "body": {"data": b64("<p>Hi <b>Gabe</b></p>")}},
                    {"mimeType": "text/plain", "body": {"data": b64("Hi Gabe, plain")}},
                ],
            },
        }

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        return Call({"messages": [{"id": "m1"}]})

    def get(self, **kw):
        return Call(self.msg)

    def send(self, userId, body):
        self.sent.append(body)
        return Call({"id": "s1"})

    def modify(self, userId, id, body):
        self.modified.append((id, body))
        return Call({})

    def drafts(self):
        outer = self

        class D:
            def create(self, userId, body):
                outer.drafted.append(body)
                return Call({"id": "d1"})

        return D()


@pytest.fixture
def gmail_cfg(config_file, monkeypatch):
    text = config_file.read_text().replace(
        "{name: email, settings: {from_address: g@test.co}}", "{name: email, settings: {backend: gmail}}"
    )
    config_file.write_text(text + "\nbriefing: {time: '07:30', days: [mon, wed]}\n")
    fake = FakeGmail()
    monkeypatch.setattr(gb, "load_credentials", lambda *a, **k: object())
    monkeypatch.setattr(gb, "build_service", lambda creds: fake)
    return load_config(config_file), fake


def test_gmail_tools(gmail_cfg):
    cfg, fake = gmail_cfg
    reg = ToolRegistry()
    load_integrations(cfg, reg)
    found = reg.get("search_email").run({"query": "is:unread"})
    assert '"unread": true' in found and "move & meet" in found
    body = reg.get("read_email").run({"id": "m1"})
    assert "Hi Gabe, plain" in body and "<b>" not in body
    reg.get("send_email").run({"to": ["a@b.c"], "subject": "Re: Budget", "body": "Yes", "reply_to_id": "m1"})
    sent = fake.sent[0]
    assert sent["threadId"] == "t1"
    raw = base64.urlsafe_b64decode(sent["raw"]).decode()
    assert "In-Reply-To: <abc@x.co>" in raw and "To: a@b.c" in raw
    reg.get("draft_email").run({"to": ["a@b.c"], "subject": "s", "body": "b"})
    assert fake.drafted and "raw" in fake.drafted[0]["message"]
    reg.get("mark_email_read").run({"id": "m1"})
    assert fake.modified == [("m1", {"removeLabelIds": ["UNREAD"]})]


def test_html_only_body_is_stripped():
    payload = {"mimeType": "text/html", "body": {"data": b64("<style>x{}</style><p>One</p><p>Two &amp; three</p>")}}
    assert gb.extract_body(payload) == "One\nTwo & three"


def test_briefer_is_read_only(gmail_cfg):
    cfg, _ = gmail_cfg
    names = [t.name for t in build_briefer(cfg, provider=ScriptedProvider([])).registry.tools()]
    assert "search_email" in names and "list_tasks" in names
    assert not {"send_email", "draft_email", "create_task", "remember", "delegate"} & set(names)


def test_run_briefing_writes_file(gmail_cfg):
    cfg, _ = gmail_cfg
    p = ScriptedProvider([tool_step(("search_email", {"query": "is:unread newer_than:1d"})), done("# Brief\n- all good")])
    text, path = run_briefing(cfg, provider=p)
    assert text.startswith("# Brief") and path.read_text().startswith("# Brief")
    assert "Budget" in results(p)[0][0].content


def test_next_run():
    tz = ZoneInfo("Europe/Dublin")
    fri_9 = datetime(2026, 11, 27, 9, 0, tzinfo=tz)  # Friday
    assert next_run(fri_9, "08:00", ["mon", "tue", "wed", "thu", "fri"]) == datetime(2026, 11, 30, 8, 0, tzinfo=tz)
    assert next_run(fri_9, "10:15", ["fri"]) == datetime(2026, 11, 27, 10, 15, tzinfo=tz)


def test_cli_chat_subcommand_keeps_message(monkeypatch, config_file):
    import gabe.cli as cli

    seen = {}
    monkeypatch.setattr(cli, "_chat", lambda cfg, args: seen.update(m=args.message, u=args.unattended) or 0)
    cli.main(["-c", str(config_file), "chat", "-m", "hi", "--unattended"])
    assert seen == {"m": "hi", "u": True}
    cli.main(["-c", str(config_file), "-m", "top"])
    assert seen["m"] == "top"


def test_email_briefing_sends_to_me_with_html(gmail_cfg):
    from gabe.briefing import email_briefing

    cfg, fake = gmail_cfg
    cfg.briefing["email_to"] = ["me", "ops@test.co"]

    class Client:
        sent = []

        def address(self):
            return "boss@test.co"

        def send(self, to, subject, body, html_body=None):
            self.sent.append((to, subject, body, html_body))
            return "s1"

    c = Client()
    assert email_briefing(cfg, "# Brief\n- **one**", client=c) == ["boss@test.co", "ops@test.co"]
    to, subject, body, html_body = c.sent[0]
    assert subject.startswith("Daily brief: ") and body.startswith("# Brief")
    assert "<strong>one</strong>" in html_body and "<li>" in html_body
    assert "send_email" in (cfg.data_dir / "audit.jsonl").read_text()


def test_email_briefing_skipped_without_recipients(gmail_cfg):
    from gabe.briefing import email_briefing

    cfg, _ = gmail_cfg
    assert email_briefing(cfg, "x", client=object()) == []


def test_gmail_send_multipart_html(gmail_cfg):
    cfg, fake = gmail_cfg
    from gabe.integrations.email import gmail_client_from_config

    gmail_client_from_config(cfg).send(["a@b.c"], "s", "plain", html_body="<p>rich</p>")
    raw = base64.urlsafe_b64decode(fake.sent[0]["raw"]).decode()
    assert "multipart/alternative" in raw and "text/html" in raw


def test_markdown_to_html():
    from gabe.markdown_email import to_html

    out = to_html("# Title\n\nSome *it* & **bold**\n1. a\n2. b\n---\n- x <y>")
    assert "<h2" in out and "<em>it</em>" in out and "&amp;" in out
    assert "<ol" in out and "<hr" in out and "x &lt;y&gt;" in out
