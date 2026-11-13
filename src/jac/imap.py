"""Read-only IMAP reader.

Only these commands are ever issued: LOGIN, EXAMINE (select with readonly), UID SEARCH,
UID FETCH of BODY.PEEK[] and LOGOUT. Nothing here can change flags, move or delete mail.
"""
from __future__ import annotations

import imaplib
import re
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

from jac.config import ImapConfig
from jac.ingest import RawMail

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def imap_date(day: date) -> str:
    """IMAP date format, independent of the OS locale."""
    return f"{day.day:02d}-{_MONTHS[day.month - 1]}-{day.year}"


class ImapReader:
    """Yields raw messages from one folder for the allowed sender domain within a date window."""

    def __init__(
        self,
        cfg: ImapConfig,
        sender_domain: str,
        password: Callable[[], str],
        connect: Callable[[str, int], Any] = imaplib.IMAP4_SSL,
        today: date | None = None,
    ) -> None:
        self._cfg = cfg
        self._domain = sender_domain
        self._password = password
        self._connect = connect
        self._today = today

    def _open(self) -> Any:
        conn = self._connect(self._cfg.host, self._cfg.port)
        conn.login(self._cfg.user, self._password())
        status, _ = conn.select(self._cfg.folder, readonly=True)
        if status != "OK":
            conn.logout()
            raise RuntimeError(f"cannot open folder {self._cfg.folder!r} read-only")
        return conn

    def check(self) -> int:
        """Log in and open the folder read-only; return the number of messages in it."""
        conn = self._open()
        try:
            status, data = conn.select(self._cfg.folder, readonly=True)
            return int(data[0]) if status == "OK" and data and data[0] else 0
        finally:
            conn.logout()

    def read(self) -> Iterator[RawMail]:
        since = imap_date((self._today or date.today()) - timedelta(days=self._cfg.window_days))
        conn = self._open()
        try:
            status, data = conn.uid("SEARCH", None, "SINCE", since, "FROM", self._domain)
            if status != "OK" or not data or not data[0]:
                return
            for uid in data[0].split():
                status, parts = conn.uid("FETCH", uid, "(BODY.PEEK[])")
                raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), None)
                if status == "OK" and raw:
                    yield RawMail(mail_id=f"imap:{self._cfg.folder}:{re.sub(r'[^0-9]', '', uid.decode())}", raw=bytes(raw))
        finally:
            conn.logout()
