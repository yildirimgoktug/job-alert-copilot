"""Mail sources. Both yield the raw RFC 822 bytes so verification sees the real headers."""
from __future__ import annotations

import base64
import email
import email.policy
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

from jac.config import GmailConfig

GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


@dataclass(frozen=True)
class RawMail:
    """One message as received: an id for the queue and the unmodified bytes."""

    mail_id: str
    raw: bytes

    def parse(self) -> EmailMessage:
        message = email.message_from_bytes(self.raw, policy=email.policy.default)
        assert isinstance(message, EmailMessage)
        return message


def read_eml_folder(folder: Path) -> Iterator[RawMail]:
    """Yield every .eml file in a folder, sorted by name."""
    for path in sorted(folder.glob("*.eml")):
        yield RawMail(mail_id=f"eml:{path.name}", raw=path.read_bytes())


def build_gmail_query(senders: list[str], today: date, window_days: int) -> str:
    """Gmail search string limited to the allowed senders and a date window."""
    after = (today - timedelta(days=window_days)).strftime("%Y/%m/%d")
    sender_clause = " OR ".join(senders)
    return f"from:({sender_clause}) after:{after}"


def read_gmail(senders: list[str], cfg: GmailConfig, base_dir: Path, today: date | None = None) -> Iterator[RawMail]:
    """Yield raw messages from the user's own Gmail with the read-only scope.

    The user supplies their own OAuth client file. The token is cached next to it. The
    scope is gmail.readonly, so this code cannot send, draft, label or delete anything.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError('Gmail support is optional: pip install "job-alert-copilot[gmail]"') from exc

    token_path = base_dir / cfg.token_file
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), [GMAIL_SCOPE])
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(base_dir / cfg.client_secret_file), [GMAIL_SCOPE])
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    query = build_gmail_query(senders, today or date.today(), cfg.window_days)
    request = service.users().messages().list(userId="me", q=query)
    while request is not None:
        response = request.execute()
        for item in response.get("messages", []):
            raw = service.users().messages().get(userId="me", id=item["id"], format="raw").execute()["raw"]
            yield RawMail(mail_id=f"gmail:{item['id']}", raw=base64.urlsafe_b64decode(raw))
        request = service.users().messages().list_next(request, response)
