"""The Slack front end of the FAQ bot.

Connects with Socket Mode (an outgoing websocket), so it works from a laptop or an
on-prem box with no public URL. For each message a customer posts where the bot can see
it (channels it has been added to, and direct messages):

    filter (bots, staff, channels, rate limit) -> FaqResponder -> policy -> post in thread
                                                               └ no answer -> escalate

Posting a reply is the `reply_on_slack` action, gated by the company's approval rules like
any other: on "ask" each drafted answer waits on the Approvals screen; on "allow" it goes
straight out. Every question and what happened to it goes to data/faq/questions.jsonl.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from gabe.audit import AuditLog
from gabe.config import Config
from gabe.faq.answer import ANSWERED, NO_FAQ, NOT_A_QUESTION, NOT_COVERED, FaqResponder, looks_like_question
from gabe.faq.settings import FaqSettings, SlackSettings
from gabe.integrations._store import JsonStore
from gabe.policy import Approver, Decision, Policy, deny_all
from gabe.providers import Provider, make_provider
from gabe.tools.registry import Risk, Tool, schema
from gabe.usage import UsageLog

log = logging.getLogger("gabe.faq.slack")

BOT_NAME = "FAQ bot"

REPLY_TOOL = Tool(
    name="reply_on_slack",
    description="Post an answer from the company FAQ to a customer's question in Slack.",
    input_schema=schema({
        "channel": {"type": "string", "description": "Where the question was asked."},
        "question": {"type": "string"},
        "reply": {"type": "string", "description": "The answer that will be posted."},
    }),
    handler=lambda **_: "",  # posting is done by the bot once the policy allows it
    risk=Risk.EXTERNAL,
    integration="faq",
)

# Message subtypes that are still a person writing something.
_PERSON_SUBTYPES = {None, "thread_broadcast", "file_share"}

MANIFEST = {
    "display_information": {"name": "Gabe", "description": "Answers customer questions from your FAQ."},
    "features": {"bot_user": {"display_name": "Gabe", "always_online": True},
                 "app_home": {"messages_tab_enabled": True, "messages_tab_read_only_enabled": False}},
    "oauth_config": {"scopes": {"bot": [
        "app_mentions:read", "channels:history", "channels:join", "channels:read", "groups:history", "groups:read",
        "im:history", "im:read", "mpim:history", "chat:write", "users:read",
    ]}},
    "settings": {
        "event_subscriptions": {"bot_events": ["message.channels", "message.groups", "message.im", "message.mpim"]},
        "interactivity": {"is_enabled": False},
        "socket_mode_enabled": True,
        "token_rotation_enabled": False,
    },
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class QuestionLog:
    """Every customer message the bot looked at, newest last (data/faq/questions.jsonl)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def add(self, **rec: Any) -> dict[str, Any]:
        rec = {"id": uuid.uuid4().hex[:10], "ts": now(), **rec}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text().splitlines()[-limit:]
        out = []
        for line in reversed(lines):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


def question_log(cfg: Config) -> QuestionLog:
    return QuestionLog(cfg.data_dir / "faq" / "questions.jsonl")


def _ok(resp: Any) -> dict[str, Any]:
    """A Slack API response as a dict (slack_sdk returns SlackResponse objects)."""
    return resp.data if hasattr(resp, "data") else dict(resp)


class SlackFaqBot:
    """Handles one Slack workspace's message events. `web` is a slack_sdk WebClient (or
    anything with the same methods); `handle(event)` is called once per event."""

    def __init__(self, load_config: Callable[[], Config], web: Any, *,
                 provider_factory: Callable[[Config], Provider] | None = None,
                 approver: Approver = deny_all,
                 on_usage: Callable[[dict[str, Any]], None] | None = None) -> None:
        self.load_config = load_config
        self.web = web
        self.provider_factory = provider_factory or (lambda cfg: make_provider(cfg.provider))
        self.approver = approver
        self.on_usage = on_usage or (lambda u: None)
        ident = _ok(web.auth_test())
        self.bot_user_id: str = ident.get("user_id", "")
        self.bot_id: str = ident.get("bot_id", "")
        self.team_id: str = ident.get("team_id", "")
        self.workspace: str = ident.get("team", "")
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._users: dict[str, dict[str, Any]] = {}
        self._channels: dict[str, str] = {}  # name -> id
        self._channel_names: dict[str, str] = {}  # id -> name
        self._channels_at = 0.0
        self._asked: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    # -- lookups ------------------------------------------------------------------

    def _load_channels(self) -> None:
        if time.time() - self._channels_at < 300:
            return
        names, cursor = {}, None
        try:
            while True:
                r = _ok(self.web.conversations_list(types="public_channel,private_channel", limit=1000,
                                                     exclude_archived=True, cursor=cursor))
                for c in r.get("channels", []):
                    names[c["name"]] = c["id"]
                cursor = (r.get("response_metadata") or {}).get("next_cursor")
                if not cursor:
                    break
        except Exception as e:
            log.warning("couldn't list Slack channels: %s", e)
        with self._lock:
            if names:
                self._channels = names
                self._channel_names = {v: k for k, v in names.items()}
            self._channels_at = time.time()

    def channels(self) -> list[dict[str, Any]]:
        """Every channel the bot can see: public ones, and private ones it has been added to."""
        out, cursor = [], None
        while True:
            r = _ok(self.web.conversations_list(types="public_channel,private_channel", limit=1000,
                                                 exclude_archived=True, cursor=cursor))
            for c in r.get("channels", []):
                out.append({"id": c["id"], "name": c.get("name", c["id"]), "private": bool(c.get("is_private")),
                            "member": bool(c.get("is_member")), "members": c.get("num_members")})
            cursor = (r.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        with self._lock:
            self._channels = {c["name"]: c["id"] for c in out}
            self._channel_names = {c["id"]: c["name"] for c in out}
            self._channels_at = time.time()
        return sorted(out, key=lambda c: c["name"])

    def join(self, refs: list[str]) -> dict[str, str]:
        """Join each channel the bot isn't in yet, so it sees the questions there. Private
        channels (and workspaces where the app can't join by itself) need an /invite."""
        known = {c["id"]: c for c in self.channels()}
        out = {}
        for ref in refs:
            cid = self.channel_id(ref)
            c = known.get(cid)
            if c is None:
                out[ref] = "not_found"
            elif c["member"]:
                out[cid] = "member"
            elif c["private"]:
                out[cid] = "needs_invite"
            else:
                try:
                    self.web.conversations_join(channel=cid)
                    out[cid] = "joined"
                except Exception as e:
                    log.info("couldn't join %s: %s", cid, e)
                    out[cid] = "needs_invite"
        return out

    def channel_id(self, ref: str) -> str:
        """A channel given as an id (C0123) or a name (#help, help)."""
        ref = ref.strip()
        if re.fullmatch(r"[CGD][A-Z0-9]{6,}", ref):
            return ref
        self._load_channels()
        return self._channels.get(ref.lstrip("#").lower(), ref)

    def channel_name(self, cid: str, channel_type: str = "") -> str:
        if channel_type == "im":
            return "direct message"
        self._load_channels()
        name = self._channel_names.get(cid)
        return f"#{name}" if name else cid

    def user(self, uid: str) -> dict[str, Any]:
        if uid not in self._users:
            info: dict[str, Any] = {"name": uid, "external": False}
            try:
                u = _ok(self.web.users_info(user=uid)).get("user") or {}
                profile = u.get("profile") or {}
                info = {
                    "name": profile.get("display_name") or u.get("real_name") or u.get("name") or uid,
                    "external": bool(u.get("is_restricted") or u.get("is_ultra_restricted") or u.get("is_stranger")
                                     or (u.get("team_id") and self.team_id and u["team_id"] != self.team_id)),
                    "bot": bool(u.get("is_bot")),
                }
            except Exception as e:  # people from other workspaces may not be visible: treat as outside
                log.info("users.info failed for %s: %s", uid, e)
                info["external"] = True
            self._users[uid] = info
        return self._users[uid]

    # -- the pipeline -----------------------------------------------------------------

    def _first_time(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen[key] = None
            while len(self._seen) > 5000:
                self._seen.popitem(last=False)
            return True

    def _within_limit(self, uid: str, per_hour: int) -> bool:
        if per_hour <= 0:
            return True
        with self._lock:
            q = self._asked[uid]
            cutoff = time.time() - 3600
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= per_hour:
                return False
            q.append(time.time())
            return True

    def _thread_history(self, channel: str, thread_ts: str, ts: str, asker: str) -> tuple[list[tuple[str, str]], bool, bool]:
        """Earlier messages in a thread, whether the bot has spoken in it, and whether someone
        other than the asker and the bot has (a person on the team took over)."""
        msgs = _ok(self.web.conversations_replies(channel=channel, ts=thread_ts, limit=50)).get("messages", [])
        history, bot_in, others = [], False, False
        for m in msgs:
            if m.get("ts") == ts:
                continue
            mine = m.get("user") == self.bot_user_id or (self.bot_id and m.get("bot_id") == self.bot_id)
            if mine:
                bot_in = True
                history.append(("you", m.get("text", "")))
            elif m.get("user") == asker:
                history.append(("customer", self._strip(m.get("text", ""))))
            elif not m.get("bot_id"):
                others = True
        return history, bot_in, others

    def _dm_history(self, channel: str, ts: str) -> list[tuple[str, str]]:
        try:
            msgs = _ok(self.web.conversations_history(channel=channel, latest=ts, inclusive=False, limit=6)).get("messages", [])
        except Exception:
            return []
        out = []
        for m in reversed(msgs):
            who = "you" if m.get("user") == self.bot_user_id or m.get("bot_id") else "customer"
            out.append((who, self._strip(m.get("text", ""))))
        return out

    def _strip(self, text: str) -> str:
        return re.sub(rf"<@{re.escape(self.bot_user_id)}>\s*", "", text or "").strip() if self.bot_user_id else (text or "").strip()

    def handle(self, event: dict[str, Any]) -> str:
        """Deal with one message event. Returns what happened, for logs and tests."""
        if event.get("type") not in ("message", "app_mention"):
            return "ignored"
        if event.get("subtype") not in _PERSON_SUBTYPES or event.get("bot_id"):
            return "ignored"
        uid, channel, ts = event.get("user"), event.get("channel"), event.get("ts")
        text = event.get("text") or ""
        if not uid or not channel or not ts or uid == self.bot_user_id or not text.strip():
            return "ignored"
        if not self._first_time(f"{channel}:{ts}"):
            return "duplicate"

        cfg = self.load_config()
        settings = FaqSettings.from_config(cfg)
        if settings is None or not settings.slack.enabled:
            return "off"
        s = settings.slack
        channel_type = event.get("channel_type") or ("im" if channel.startswith("D") else "channel")
        if channel_type == "im":
            if not s.direct_messages:
                return "ignored"
        elif s.channels and channel not in {self.channel_id(c) for c in s.channels}:
            return "ignored"
        if uid in s.staff:
            return "staff"
        person = self.user(uid)
        if person.get("bot"):
            return "ignored"
        if s.answer == "external" and not (person["external"] or (event.get("user_team") and event["user_team"] != self.team_id)):
            return "staff"

        mentioned = bool(self.bot_user_id) and f"<@{self.bot_user_id}>" in text
        question = self._strip(text)
        thread_ts = event.get("thread_ts")
        history: list[tuple[str, str]] = []
        if thread_ts and thread_ts != ts:
            history, bot_in, others = self._thread_history(channel, thread_ts, ts, uid)
            if not mentioned and not (s.follow_ups and bot_in and not others):
                return "ignored"  # someone else's thread, or a person has taken it over
        elif channel_type == "im":
            history = self._dm_history(channel, ts)
        reply_ts = thread_ts or (ts if channel_type != "im" else None)

        where = self.channel_name(channel, channel_type)
        qlog = question_log(cfg)
        base = {"channel": channel, "channel_name": where, "user": uid, "user_name": person["name"],
                "text": question, "message_ts": ts, "thread_ts": reply_ts}

        if not self._within_limit(uid, s.per_user_per_hour):
            qlog.add(**base, outcome="rate_limited")
            return "rate_limited"

        usage_log = UsageLog(cfg.data_dir / "usage.jsonl")

        def count(u: dict[str, Any]) -> None:
            usage_log.record(user=person["name"], task="faq", agent=BOT_NAME, usage=u)
            self.on_usage(u)

        responder = FaqResponder(settings, self.provider_factory(cfg), assistant=cfg.assistant_name,
                                 company=str(cfg.company.get("name", "")), on_usage=count)
        try:
            reply = responder.answer(question, history)
        except Exception as e:
            log.exception("FAQ answer failed")
            qlog.add(**base, outcome="error", error=f"{type(e).__name__}: {e}")
            return "error"

        if reply.outcome == ANSWERED:
            return self._post_answer(cfg, s, base, reply, qlog)
        if reply.outcome == NOT_A_QUESTION and not mentioned:
            return NOT_A_QUESTION  # chatter: not logged
        asked = looks_like_question(question)
        if reply.outcome == NOT_COVERED or (asked and (reply.outcome == NO_FAQ or mentioned)):
            self._escalate(cfg, s, base)
            qlog.add(**base, outcome="unanswered")
            return "unanswered"
        return reply.outcome

    def _post_answer(self, cfg: Config, s: SlackSettings, base: dict[str, Any], reply, qlog: QuestionLog) -> str:
        args = {"channel": base["channel_name"], "question": base["text"], "reply": reply.text}
        used = [e.question or e.answer[:80] for e in reply.used]
        audit = AuditLog(cfg.data_dir / "audit.jsonl")
        decision = Policy.from_config(cfg.policy).decide(REPLY_TOOL)

        def record(outcome: str, error: bool = False) -> None:
            audit.record(agent=BOT_NAME, chain=BOT_NAME, tool=REPLY_TOOL.name, input=args, outcome=outcome, error=error)

        if decision is Decision.DENY:
            record("denied_by_policy", True)
            qlog.add(**base, outcome="blocked", reply=reply.text, used=used)
            return "blocked"
        if decision is Decision.ASK:
            verdict = self.approver(REPLY_TOOL, args)
            if not verdict:
                record("declined_by_user", True)
                qlog.add(**base, outcome="declined", reply=reply.text, used=used, note=getattr(verdict, "note", ""))
                return "declined"
        try:
            self.web.chat_postMessage(channel=base["channel"], thread_ts=base["thread_ts"], text=args["reply"],
                                      unfurl_links=False, unfurl_media=False)
        except Exception as e:
            record("error", True)
            qlog.add(**base, outcome="error", reply=args["reply"], used=used, error=f"{type(e).__name__}: {e}")
            return "error"
        record("ok")
        qlog.add(**base, outcome=ANSWERED, reply=args["reply"], used=used, edited=args["reply"] != reply.text)
        return ANSWERED

    def _escalate(self, cfg: Config, s: SlackSettings, base: dict[str, Any]) -> None:
        """No answer in the FAQ: tell the team, make a task, and/or tell the customer someone will reply."""
        link = ""
        try:
            link = _ok(self.web.chat_getPermalink(channel=base["channel"], message_ts=base["message_ts"])).get("permalink", "")
        except Exception:
            pass
        quoted = "\n".join("> " + line for line in base["text"].splitlines())
        if s.notify_channel:
            where = "a direct message" if base["channel_name"] == "direct message" else f"<#{base['channel']}>"
            try:
                self.web.chat_postMessage(
                    channel=self.channel_id(s.notify_channel), unfurl_links=False,
                    text=f"Unanswered question from <@{base['user']}> in {where}:\n{quoted}" + (f"\n{link}" if link else ""))
            except Exception as e:
                log.warning("couldn't post to %s: %s", s.notify_channel, e)
        if s.create_task:
            task = {
                "id": uuid.uuid4().hex[:8],
                "title": f"Answer {base['user_name']}'s question in Slack",
                "owner": s.task_owner or "Unassigned",
                "due": None,
                "notes": f"{base['text']}\n\n{base['channel_name']}" + (f" · {link}" if link else ""),
                "project": "Customer questions",
                "status": "open",
                "created": now(),
                "source": "slack",
            }
            JsonStore(cfg.data_dir / "tasks" / "tasks.json", {"tasks": []}).update(lambda d: d["tasks"].append(task))
        if s.holding_reply:
            try:
                self.web.chat_postMessage(channel=base["channel"], thread_ts=base["thread_ts"], text=s.holding_reply)
            except Exception as e:
                log.warning("couldn't post the holding reply: %s", e)


class SlackService:
    """Keeps the bot connected while Gabe runs: connects when the bot is turned on and both
    tokens are there, reconnects when they change, disconnects when it's turned off."""

    RETRY_AFTER = 60

    def __init__(self, load_config: Callable[[], Config], *, approver: Approver = deny_all,
                 on_usage: Callable[[dict[str, Any]], None] | None = None,
                 provider_factory: Callable[[Config], Provider] | None = None) -> None:
        self.load_config = load_config
        self.approver = approver
        self.on_usage = on_usage
        self.provider_factory = provider_factory
        self._client = None
        self._bot: SlackFaqBot | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._sig: tuple | None = None
        self._failed: tuple[tuple, float] | None = None
        self._state: dict[str, Any] = {"state": "off"}
        self._lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        out = dict(self._state)
        if out.get("state") == "connected" and self._client is not None and not self._client.is_connected():
            out["state"] = "connecting"
        return out

    def sync(self) -> None:
        with self._lock:
            try:
                cfg = self.load_config()
                settings = FaqSettings.from_config(cfg)
            except Exception as e:
                self._state = {"state": "error", "error": f"{e}"}
                return
            s = settings.slack if settings else None
            if not s or not s.enabled:
                self._stop()
                self._state = {"state": "off"}
                return
            if not (s.bot_token and s.app_token):
                self._stop()
                self._state = {"state": "missing_tokens", "bot_token": bool(s.bot_token), "app_token": bool(s.app_token)}
                return
            sig = (s.bot_token, s.app_token)
            if sig == self._sig and self._client is not None:
                return
            if self._failed and self._failed[0] == sig and time.time() - self._failed[1] < self.RETRY_AFTER:
                return
            self._stop()
            self._start(s, sig)

    def _start(self, s: SlackSettings, sig: tuple) -> None:
        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError:
            self._state = {"state": "error", "error": "The Slack package isn't installed (pip install 'gabe[slack]')."}
            self._failed = (sig, time.time())
            return
        self._state = {"state": "connecting"}
        try:
            web = WebClient(token=s.bot_token)
            bot = SlackFaqBot(self.load_config, web, approver=self.approver, on_usage=self.on_usage,
                              provider_factory=self.provider_factory)
            client = SocketModeClient(app_token=s.app_token, web_client=web, auto_reconnect_enabled=True)
            pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="slack-faq")

            def on_request(c, req):
                c.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))  # ack within 3 s
                if req.type == "events_api":
                    event = (req.payload or {}).get("event") or {}
                    pool.submit(self._safe_handle, bot, event)

            client.socket_mode_request_listeners.append(on_request)
            client.connect()
        except Exception as e:
            msg = str(e)
            m = re.search(r"'error': '([a-z_]+)'", msg)
            friendly = {"invalid_auth": "Slack didn't accept a token.", "not_authed": "Slack didn't accept a token.",
                        "account_inactive": "That Slack app has been removed."}.get(m.group(1) if m else "", msg)
            self._state = {"state": "error", "error": friendly}
            self._failed = (sig, time.time())
            log.warning("Slack FAQ bot couldn't connect: %s", msg)
            return
        self._client, self._bot, self._pool, self._sig, self._failed = client, bot, pool, sig, None
        self._state = {"state": "connected", "workspace": bot.workspace, "bot_user": bot.bot_user_id}
        log.info("Slack FAQ bot connected to %s", bot.workspace)

    @staticmethod
    def _safe_handle(bot: SlackFaqBot, event: dict[str, Any]) -> None:
        try:
            outcome = bot.handle(event)
            log.debug("slack event %s -> %s", event.get("ts"), outcome)
        except Exception:
            log.exception("Slack FAQ bot failed on an event")

    def _stop(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        if self._pool is not None:
            self._pool.shutdown(wait=False)
        self._client = self._bot = self._pool = None
        self._sig = None

    def bot(self) -> SlackFaqBot:
        """The connected bot, for listing and joining channels."""
        if self._bot is None:
            raise RuntimeError("Slack isn't connected.")
        return self._bot

    def stop(self) -> None:
        with self._lock:
            self._stop()
            self._state = {"state": "off"}
