"""Gmail / Google Workspace backend for the email integration (Gmail API, OAuth).

Setup: see docs/GMAIL_SETUP.md. Authorise once with `gabe auth gmail`; the
refresh token is stored in the data dir and reused.
"""

from __future__ import annotations

import base64
import html
import re
from email.message import EmailMessage
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
MAX_BODY_CHARS = 20_000


def load_credentials(credentials_file: Path, token_file: Path, interactive: bool = False,
                     scopes: list[str] | None = None, not_signed_in: str | None = None):
    """Google OAuth credentials from `token_file`, refreshed as needed; with `interactive`, a
    browser sign-in when there are none. Gmail's scopes by default (the calendar passes its own)."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    scopes = scopes or SCOPES
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    if creds and creds.valid:
        return creds
    if not interactive:
        raise RuntimeError(not_signed_in or "Gmail isn't signed in. Use Sign in with Google in Settings → Email.")
    if not credentials_file.exists():
        raise FileNotFoundError(
            f"Google sign-in isn't set up in this copy of Gabe (no OAuth client at {credentials_file})."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), scopes)
    # Google's page opens in the system browser (it blocks sign-in inside embedded app windows).
    creds = flow.run_local_server(
        port=0,
        prompt="consent select_account",
        authorization_prompt_message="",
        success_message="Signed in. You can close this tab and go back to Gabe.",
        timeout_seconds=300,
    )
    if creds is None:
        raise TimeoutError("Google sign-in wasn't finished. Try again.")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    token_file.chmod(0o600)
    return creds


def build_service(creds):
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _headers(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in payload.get("headers", [])}


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()


def extract_body(payload: dict[str, Any]) -> str:
    """Prefer text/plain; fall back to text/html stripped of tags."""
    plain, rich = [], []

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_strip_html(_decode(data)))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(plain) if plain else "\n".join(rich)


class GmailClient:
    """Thin wrapper over the Gmail API. `service_factory` is called lazily so
    Gabe starts even before Gmail is authorised (tools then return an error)."""

    def __init__(self, service_factory) -> None:
        self._factory = service_factory
        self._service = None

    @property
    def svc(self):
        if self._service is None:
            self._service = self._factory()
        return self._service

    def search(self, query: str, max_results: int = 10) -> list[dict[str, Any]]:
        listing = (
            self.svc.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        )
        out = []
        for ref in listing.get("messages", []):
            m = (
                self.svc.users()
                .messages()
                .get(userId="me", id=ref["id"], format="metadata",
                     metadataHeaders=["From", "To", "Subject", "Date"])
                .execute()
            )
            h = _headers(m.get("payload", {}))
            out.append({
                "id": m["id"],
                "thread_id": m.get("threadId"),
                "from": h.get("from"),
                "to": h.get("to"),
                "subject": h.get("subject"),
                "date": h.get("date"),
                "unread": "UNREAD" in m.get("labelIds", []),
                "snippet": html.unescape(m.get("snippet", "")),
            })
        return out

    def read(self, message_id: str) -> dict[str, Any]:
        m = self.svc.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = m.get("payload", {})
        h = _headers(payload)
        body = extract_body(payload)
        truncated = len(body) > MAX_BODY_CHARS
        return {
            "id": m["id"],
            "thread_id": m.get("threadId"),
            "from": h.get("from"),
            "to": h.get("to"),
            "cc": h.get("cc"),
            "subject": h.get("subject"),
            "date": h.get("date"),
            "labels": m.get("labelIds", []),
            "body": body[:MAX_BODY_CHARS] + ("\n[...truncated]" if truncated else ""),
        }

    def address(self) -> str:
        """The connected mailbox's own address."""
        return self.svc.users().getProfile(userId="me").execute()["emailAddress"]

    def _compose(self, to, subject, body, cc=None, reply_to_id=None, html_body=None) -> dict[str, Any]:
        msg = EmailMessage()
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg.set_content(body)
        if html_body:
            msg.add_alternative(html_body, subtype="html")
        out: dict[str, Any] = {}
        if reply_to_id:
            orig = (
                self.svc.users().messages()
                .get(userId="me", id=reply_to_id, format="metadata",
                     metadataHeaders=["Message-ID", "References"])
                .execute()
            )
            h = _headers(orig.get("payload", {}))
            if h.get("message-id"):
                msg["In-Reply-To"] = h["message-id"]
                msg["References"] = f"{h.get('references', '')} {h['message-id']}".strip()
            out["threadId"] = orig.get("threadId")
        out["raw"] = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return out

    def draft(self, to, subject, body, cc=None, reply_to_id=None) -> str:
        message = self._compose(to, subject, body, cc, reply_to_id)
        d = self.svc.users().drafts().create(userId="me", body={"message": message}).execute()
        return d["id"]

    def send(self, to, subject, body, cc=None, reply_to_id=None, html_body=None) -> str:
        message = self._compose(to, subject, body, cc, reply_to_id, html_body)
        sent = self.svc.users().messages().send(userId="me", body=message).execute()
        return sent["id"]

    def mark_read(self, message_id: str) -> None:
        self.svc.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
