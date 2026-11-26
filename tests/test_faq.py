import base64
import json

import pytest
import yaml

from gabe.config import load_config
from gabe.faq.answer import ANSWERED, NOT_A_QUESTION, NOT_COVERED, FaqResponder
from gabe.faq.library import FaqLibrary, safe_name
from gabe.faq.parse import parse_document
from gabe.faq.settings import FaqSettings
from gabe.faq.slack import SlackFaqBot, question_log
from gabe.policy import Declined
from gabe.providers.base import StepResult
from tests.conftest import ScriptedProvider

FAQ_TXT = b"""# Billing
Q: How do I get a refund?
A: Email billing@example.com within 30 days of purchase.

Q: Can I pay by invoice?
A: Yes, on annual plans.

# Account
Q: How do I reset my password?
A: Click "Forgot password" on the sign-in page.
"""


def said(reply=None, used=(1,), why="not_covered", redirect=None):
    body = {"reply": reply, "used": list(used)} if reply else {"reply": None, "why": why}
    if redirect:
        body["redirect"] = redirect
    return StepResult(json.dumps(body), [], "done")


# -- parsing ---------------------------------------------------------------------

def test_parses_q_and_a_markers_with_sections():
    e = parse_document("help.txt", FAQ_TXT)
    assert [(x.section, x.question) for x in e] == [
        ("Billing", "How do I get a refund?"), ("Billing", "Can I pay by invoice?"), ("Account", "How do I reset my password?")]
    assert e[0].answer.startswith("Email billing@") and e[0].source == "help.txt" and e[0].id == "help-1"


def test_parses_markdown_headings_and_question_lines():
    md = b"# Shipping\n\n## Do you ship abroad?\nYes, to 40 countries.\n\n## How long does it take?\nAbout a week.\n"
    assert [(x.section, x.question, x.answer) for x in parse_document("a.md", md)] == [
        ("Shipping", "Do you ship abroad?", "Yes, to 40 countries."), ("Shipping", "How long does it take?", "About a week.")]
    plain = b"FAQ\n\n1. What are your hours?\n9 to 5.\n\n2. **Where are you?**\nDublin.\n"
    assert [(x.question, x.answer) for x in parse_document("b.txt", plain)] == [
        ("What are your hours?", "9 to 5."), ("Where are you?", "Dublin.")]


def test_parses_csv_json_and_falls_back_to_paragraphs():
    csv_ = b"Question,Answer,Category\nIs there a free trial?,Yes - 14 days,Plans\n"
    assert parse_document("f.csv", csv_)[0].question == "Is there a free trial?"
    assert parse_document("f.csv", csv_)[0].section == "Plans"
    js = json.dumps({"faqs": [{"Question": "Do you have an API?", "Answer": "Yes."}]}).encode()
    assert parse_document("f.json", js)[0].answer == "Yes."
    prose = b"We are based in Dublin.\n\nWe integrate with Slack and Teams."
    e = parse_document("about.txt", prose)
    assert len(e) == 1 and e[0].question == "" and "Teams" in e[0].answer


def test_parses_word_documents():
    import io
    import zipfile

    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    def p(text, style=None):
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"
    xml = f'<w:document {w}><w:body>{p("Can I cancel?", "Heading2")}{p("Yes, any time.")}' \
          f'{p("Do you offer discounts?", "Heading2")}{p("For charities.")}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    e = parse_document("faq.docx", buf.getvalue())
    assert [(x.question, x.answer) for x in e] == [("Can I cancel?", "Yes, any time."), ("Do you offer discounts?", "For charities.")]


def test_library_add_list_remove(tmp_path):
    lib = FaqLibrary(tmp_path / "faq")
    doc = lib.add("../../Help Centre.TXT", FAQ_TXT)
    assert doc.name == "Help Centre.txt" and (tmp_path / "faq" / "Help Centre.txt").exists()
    assert len(lib.entries()) == 3
    with pytest.raises(ValueError):
        lib.add("x.pdf", b"%PDF")
    with pytest.raises(ValueError):
        lib.add("empty.txt", b"   \n")
    assert lib.remove("Help Centre.txt") and lib.entries() == []
    with pytest.raises(ValueError):
        safe_name("noext")


# -- answering -------------------------------------------------------------------

def _settings(tmp_path, **over):
    raw = {"folder": str(tmp_path / "faq"), **over}
    s = FaqSettings.from_dict(raw, tmp_path)
    FaqLibrary(s.folder).add("help.txt", FAQ_TXT)
    return s


def test_small_faq_goes_whole_into_the_cached_system_prompt(tmp_path):
    p = ScriptedProvider([said("Email billing@example.com within 30 days.", used=[1])])
    r = FaqResponder(_settings(tmp_path), p, company="Acme").answer("how can I get my money back")
    assert r.outcome == ANSWERED and r.used[0].question == "How do I get a refund?"
    system, tools = p.systems[0]
    assert "Acme's customers" in system and "Can I pay by invoice?" in system and tools == []


def test_unreadable_or_cut_off_output_is_never_posted(tmp_path):
    for step in (StepResult("Sure! Refunds are easy", [], "done"), StepResult('{"reply": "Ye', [], "max_tokens")):
        r = FaqResponder(_settings(tmp_path), ScriptedProvider([step])).answer("refund?")
        assert r.outcome == NOT_COVERED and r.text is None


def test_large_faq_is_searched_first_and_skips_the_model_when_nothing_matches(tmp_path):
    s = _settings(tmp_path, full_context_chars=0)
    p = ScriptedProvider([said("Yes, on annual plans.", used=[1])])
    r = FaqResponder(s, p).answer("can I pay with an invoice?")
    assert r.outcome == ANSWERED and [e.question for e in r.candidates][0] == "Can I pay by invoice?"
    assert "<faq>" not in p.systems[0][0]
    quiet = ScriptedProvider([])
    assert FaqResponder(s, quiet).answer("what's the weather on mars?").outcome == NOT_COVERED
    assert FaqResponder(s, quiet).answer("lol nice").outcome == NOT_A_QUESTION
    assert quiet.systems == []


# -- Slack ---------------------------------------------------------------------------

class FakeSlack:
    def __init__(self, users=None, replies=None):
        self.posts = []
        self.users = users or {}
        self.replies = replies or []

    def auth_test(self):
        return {"user_id": "UBOT", "bot_id": "BBOT", "team_id": "T1", "team": "Acme"}

    def users_info(self, user):
        return {"user": self.users.get(user, {"id": user, "name": user, "team_id": "T1"})}

    def conversations_list(self, **kw):
        return {"channels": [{"id": "CHELP", "name": "help", "is_member": True},
                             {"id": "CTEAM", "name": "support-team", "is_member": "CTEAM" in self.joined},
                             {"id": "GVIP", "name": "vip", "is_private": True}]}

    joined = ()

    def conversations_join(self, channel):
        self.joined = (*self.joined, channel)
        return {"ok": True}

    def conversations_replies(self, **kw):
        return {"messages": self.replies}

    def conversations_history(self, **kw):
        return {"messages": []}

    def chat_getPermalink(self, **kw):
        return {"permalink": "https://acme.slack.com/p1"}

    def chat_postMessage(self, **kw):
        self.posts.append(kw)
        return {"ok": True}


@pytest.fixture
def slack_cfg(tmp_path):
    def make(slack=None, policy=None):
        (tmp_path / "faq").mkdir(exist_ok=True)
        (tmp_path / "faq" / "help.txt").write_bytes(FAQ_TXT)
        raw = {"company": {"name": "Acme"}, "data_dir": "data",
               "integrations": ["tasks", {"name": "faq", "settings": {"slack": {"enabled": True, **(slack or {})}}}],
               "policy": policy if policy is not None else {"tools": {"reply_on_slack": "allow"}}}
        path = tmp_path / "gabe.yaml"
        path.write_text(yaml.safe_dump(raw))
        return lambda: load_config(path)
    return make


def msg(text, user="UCUST", ts="1.1", channel="CHELP", **kw):
    return {"type": "message", "user": user, "text": text, "ts": ts, "channel": channel, "channel_type": "channel", **kw}


def bot(load, web, script, **kw):
    provider = ScriptedProvider(script)
    return SlackFaqBot(load, web, provider_factory=lambda cfg: provider, **kw), provider


def test_answers_a_customer_in_the_thread_and_logs_it(slack_cfg):
    load, web = slack_cfg(), FakeSlack()
    b, _ = bot(load, web, [said("Email billing@example.com within 30 days.")])
    assert b.handle(msg("<@UBOT> how do I get a refund?")) == ANSWERED
    assert web.posts == [{"channel": "CHELP", "thread_ts": "1.1", "text": "Email billing@example.com within 30 days.",
                          "unfurl_links": False, "unfurl_media": False}]
    q = question_log(load()).recent()[0]
    assert q["outcome"] == "answered" and q["text"] == "how do I get a refund?" and q["channel_name"] == "#help"
    assert b.handle(msg("<@UBOT> how do I get a refund?")) == "duplicate"


def test_ignores_bots_its_own_messages_staff_and_other_channels(slack_cfg):
    web = FakeSlack(users={"UGUEST": {"id": "UGUEST", "is_restricted": True, "team_id": "T1"}})
    b, p = bot(slack_cfg({"staff": ["USTAFF"], "channels": ["#help"]}), web, [])
    assert b.handle(msg("hi?", user="UBOT")) == "ignored"
    assert b.handle(msg("hi?", bot_id="B2", ts="2")) == "ignored"
    assert b.handle(msg("edited?", subtype="message_changed", ts="3")) == "ignored"
    assert b.handle(msg("refund?", user="USTAFF", ts="4")) == "staff"
    assert b.handle(msg("refund?", channel="CRANDOM", ts="5")) == "ignored"
    b2, _ = bot(slack_cfg({"answer": "external"}), web, [said("Yes.")])
    assert b2.handle(msg("refund?", user="UMEMBER", ts="6")) == "staff"
    assert b2.handle(msg("refund?", user="UGUEST", ts="7")) == ANSWERED
    assert p.systems == []


def test_asks_a_person_first_when_the_rules_say_ask(slack_cfg):
    asked = []

    def approver(tool, args):
        asked.append(dict(args))
        if len(asked) == 1:
            args["reply"] = "Edited answer."
            return True
        return Declined("not now")

    load, web = slack_cfg(policy={}), FakeSlack()  # default: anything external asks first
    b, _ = bot(load, web, [said("Draft."), said("Draft 2.")], approver=approver)
    assert b.handle(msg("refund?")) == ANSWERED and web.posts[0]["text"] == "Edited answer."
    assert asked[0] == {"channel": "#help", "question": "refund?", "reply": "Draft."}
    assert b.handle(msg("invoice?", ts="9")) == "declined" and len(web.posts) == 1
    audit = [json.loads(x) for x in (load().data_dir / "audit.jsonl").read_text().splitlines()]
    assert [a["outcome"] for a in audit] == ["ok", "declined_by_user"]


def test_unanswered_questions_go_to_the_team(slack_cfg):
    load = slack_cfg({"unanswered": {"notify_channel": "#support-team", "create_task": True, "task_owner": "Sam",
                                     "reply": "Thanks! Someone from the team will reply here."}})
    web = FakeSlack()
    b, _ = bot(load, web, [said(why="not_covered"), said(why="not_a_question")])
    assert b.handle(msg("do you sell gift cards?")) == "unanswered"
    notify, holding = web.posts
    assert notify["channel"] == "CTEAM" and "gift cards" in notify["text"] and "https://acme.slack.com/p1" in notify["text"]
    assert (holding["channel"], holding["thread_ts"], holding["text"]) == (
        "CHELP", "1.1", "Thanks! Someone from the team will reply here.")  # a fixed reply is used as it is
    task = json.loads((load().data_dir / "tasks" / "tasks.json").read_text())["tasks"][0]
    assert task["owner"] == "Sam" and task["source"] == "slack" and "gift cards" in task["notes"]
    assert b.handle(msg("thanks all, great launch", ts="2")) == NOT_A_QUESTION and len(web.posts) == 2


def test_follow_ups_only_in_its_own_threads_until_a_person_steps_in(slack_cfg):
    load = slack_cfg()
    thread = [{"ts": "1.0", "user": "UCUST", "text": "refund?"}, {"ts": "1.05", "user": "UBOT", "bot_id": "BBOT", "text": "Email billing."}]
    web = FakeSlack(replies=thread)
    b, p = bot(load, web, [said("Within 30 days.")])
    assert b.handle(msg("how long do I have?", ts="1.2", thread_ts="1.0")) == ANSWERED
    assert "Customer: refund?" in p.log[0][1] and "You: Email billing." in p.log[0][1]
    web.replies = thread + [{"ts": "1.3", "user": "USTAFF", "text": "I'll take this one"}]
    assert b.handle(msg("and after that?", ts="1.4", thread_ts="1.0")) == "ignored"
    web.replies = [{"ts": "5.0", "user": "UOTHER", "text": "anyone know?"}]
    assert b.handle(msg("same question", ts="5.1", thread_ts="5.0")) == "ignored"


def test_rate_limit_per_person(slack_cfg):
    b, _ = bot(slack_cfg({"per_user_per_hour": 1}), FakeSlack(), [said("Yes.")])
    assert b.handle(msg("refund?")) == ANSWERED
    assert b.handle(msg("invoice?", ts="2")) == "rate_limited"


def test_off_until_turned_on(slack_cfg):
    b, _ = bot(slack_cfg({"enabled": False}), FakeSlack(), [])
    assert b.handle(msg("refund?")) == "off"


# -- search_faq for Gabe and the team ---------------------------------------------------

def test_search_faq_tool(slack_cfg):
    from gabe.app import base_registry

    tool = base_registry(slack_cfg()()).get("search_faq")
    hits = json.loads(tool.run({"query": "forgot my password"}))
    assert hits[0]["question"] == "How do I reset my password?"


# -- the app ----------------------------------------------------------------------------

def test_app_faq_screen(config_file, monkeypatch):
    from fastapi.testclient import TestClient

    import gabe.web.server as server

    provider = ScriptedProvider([said("Yes, on annual plans.")])
    monkeypatch.setattr(server, "faq_provider", lambda cfg: provider)
    c = TestClient(server.create_app(config_file, run_schedules=False, run_slack=False))
    assert c.get("/api/faq").json() == {"enabled": False}
    assert c.post("/api/faq/enable").json()["documents"] == []

    up = c.post("/api/faq/documents", json={"name": "help.txt", "content_b64": base64.b64encode(FAQ_TXT).decode()})
    assert up.json()["questions"] == 3
    bad = c.post("/api/faq/documents", json={"name": "x.pdf", "content_b64": base64.b64encode(b"x").decode()})
    assert bad.status_code == 400 and "Can't read" in bad.json()["detail"]
    assert [i["question"] for i in c.get("/api/faq/documents/help.txt").json()["items"]][1] == "Can I pay by invoice?"

    tried = c.post("/api/faq/try", json={"question": "invoice?"}).json()
    assert tried["outcome"] == "answered" and tried["text"] == "Yes, on annual plans."

    saved = c.put("/api/faq/settings", json={"tone": "Warm.", "model": "sonnet", "slack": {
        "enabled": True, "channels": ["#help"], "answer": "external", "notify_channel": "#team", "create_task": True}}).json()
    assert saved["tone"] == "Warm." and saved["slack"]["channels"] == ["#help"] and saved["status"]["state"] == "off"
    raw = yaml.safe_load(config_file.read_text())
    faq = next(i for i in raw["integrations"] if isinstance(i, dict) and i["name"] == "faq")
    assert faq["settings"]["slack"]["unanswered"]["notify_channel"] == "#team" and faq["settings"]["model"] == "sonnet"
    assert "bot_token" not in json.dumps(raw)

    assert c.put("/api/faq/tokens", json={"bot_token": "nope"}).status_code == 400
    rules = c.get("/api/settings").json()["policy"]["tools"]
    assert {"reply_on_slack", "search_faq"} <= {r["name"] for r in rules}
    assert saved["reply_rule"] == "ask"
    assert c.delete("/api/faq/documents/help.txt").json() == {"ok": True}


def test_service_connects_acks_and_hands_events_to_the_bot(slack_cfg, monkeypatch):
    import time
    from types import SimpleNamespace

    import slack_sdk
    import slack_sdk.socket_mode as sm

    from gabe.faq.slack import SlackService

    web = FakeSlack()
    acks, clients = [], []

    class FakeSocket:
        def __init__(self, app_token, web_client, **kw):
            self.socket_mode_request_listeners = []
            self.closed = False
            clients.append(self)

        def connect(self):
            pass

        def is_connected(self):
            return not self.closed

        def close(self):
            self.closed = True

        def send_socket_mode_response(self, r):
            acks.append(r.envelope_id)

    monkeypatch.setattr(slack_sdk, "WebClient", lambda token: web)
    monkeypatch.setattr(sm, "SocketModeClient", FakeSocket)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    load = slack_cfg()
    provider = ScriptedProvider([said("Email billing.")])
    svc = SlackService(load, provider_factory=lambda cfg: provider)
    svc.sync()
    assert svc.status() == {"state": "missing_tokens", "bot_token": False, "app_token": False}

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-1")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-1")
    svc.sync()
    assert svc.status()["state"] == "connected" and svc.status()["workspace"] == "Acme"
    svc.sync()
    assert len(clients) == 1  # nothing changed: same connection
    listener = clients[0].socket_mode_request_listeners[0]
    listener(clients[0], SimpleNamespace(envelope_id="e1", type="events_api", payload={"event": msg("refund?")}))
    assert acks == ["e1"]
    deadline = time.time() + 3
    while not web.posts and time.time() < deadline:
        time.sleep(0.02)
    assert web.posts[0]["text"] == "Email billing."

    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-2")
    svc.sync()
    assert clients[0].closed and len(clients) == 2
    svc.stop()
    assert clients[1].closed and svc.status() == {"state": "off"}


def test_lists_and_joins_channels(slack_cfg):
    web = FakeSlack()
    b, _ = bot(slack_cfg(), web, [])
    assert [(c["name"], c["member"], c["private"]) for c in b.channels()] == [
        ("help", True, False), ("support-team", False, False), ("vip", False, True)]
    assert b.join(["CHELP", "#support-team", "GVIP", "#nope"]) == {
        "CHELP": "member", "CTEAM": "joined", "GVIP": "needs_invite", "#nope": "not_found"}
    assert web.joined == ("CTEAM",)
    assert b.handle(msg("refund?", channel="CTEAM")) != "ignored"  # every channel it's in by default


def test_says_when_it_cant_answer_and_where_to_look(tmp_path, slack_cfg):
    # The model words it, using only the places the company listed.
    s = _settings(tmp_path, where_to_look="Help centre: https://help.acme.example")
    p = ScriptedProvider([said(why="not_covered", redirect="I don't have that one. Try https://help.acme.example.")])
    r = FaqResponder(s, p).answer("do you sell gift cards?")
    assert r.outcome == NOT_COVERED and r.redirect == "I don't have that one. Try https://help.acme.example."
    assert "<where_to_look>" in p.systems[0][0] and "https://help.acme.example" in p.systems[0][0]
    # No model call (nothing matched) or nothing usable from it: a plain message with the same pointers.
    big = _settings(tmp_path, where_to_look="Email help@acme.example", full_context_chars=0)
    r = FaqResponder(big, ScriptedProvider([])).answer("what's the weather on mars?")
    assert r.redirect == "Sorry, I don't have the answer to that. You might find it here:\nEmail help@acme.example"
    r = FaqResponder(s, ScriptedProvider([said(why="not_covered")])).answer("gift cards?")
    assert r.redirect.startswith("Sorry, I don't have the answer to that.")

    # In Slack it's posted in the thread, under the approval rules, saying the team has it when they do.
    load, web = slack_cfg({"unanswered": {"create_task": True}}), FakeSlack()
    b, _ = bot(load, web, [said(why="not_covered", redirect="I don't know that, sorry.")])
    assert b.handle(msg("do you sell gift cards?")) == "unanswered"
    assert web.posts[0]["thread_ts"] == "1.1"
    assert web.posts[0]["text"] == "I don't know that, sorry.\n\nI've passed your question on to the team."
    q = question_log(load()).recent()[0]
    assert q["outcome"] == "unanswered" and q["posted"] is True

    asked = []
    load, web = slack_cfg(policy={}), FakeSlack()
    b, _ = bot(load, web, [said(why="not_covered", redirect="Not sure, sorry.")],
               approver=lambda tool, args: asked.append(args["reply"]) or False)
    assert b.handle(msg("gift cards?")) == "unanswered" and asked == ["Not sure, sorry."] and web.posts == []

    load, web = slack_cfg({"unanswered": {"say_so": False}}), FakeSlack()
    b, _ = bot(load, web, [said(why="not_covered", redirect="Not sure.")])
    assert b.handle(msg("gift cards?")) == "unanswered" and web.posts == []
