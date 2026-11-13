"""Tool configuration (config.yaml). Everything has a safe default; the file is optional."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

AUTHSERV_PRESETS: dict[str, list[str]] = {
    "gmail": ["mx.google.com"],
    "outlook": ["*.protection.outlook.com", "mx.microsoft.com"],
    "yahoo": ["*.mail.yahoo.com"],
    "fastmail": ["*.messagingengine.com"],
}
IMAP_PRESETS: dict[str, tuple[str, int]] = {
    "gmail": ("imap.gmail.com", 993),
    "outlook": ("outlook.office365.com", 993),
    "yahoo": ("imap.mail.yahoo.com", 993),
    "fastmail": ("imap.fastmail.com", 993),
}


class ConfigError(Exception):
    """A setting is missing or wrong; the message says what to change."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImapConfig(_Strict):
    """Read-only IMAP access. The password lives in the OS keyring, never here."""

    host: str = ""
    port: int = 993
    user: str = ""
    folder: str = "INBOX"
    window_days: int = 7


class MailConfig(_Strict):
    """Where mail comes from and whose Authentication-Results header we trust."""

    method: Literal["eml", "imap", "gmail"] = "eml"
    provider: str = ""
    trusted_authserv_ids: list[str] = Field(default_factory=list)
    allowed_senders: list[str] | None = None
    eml_folder: str = "mail"
    imap: ImapConfig = Field(default_factory=ImapConfig)

    def authserv_ids(self) -> list[str]:
        """Explicit ids if set, else the provider preset. Empty means verification must refuse."""
        return self.trusted_authserv_ids or AUTHSERV_PRESETS.get(self.provider, [])


class FetchConfig(_Strict):
    user_agent: str = "job-alert-copilot/0.1 (personal, low-volume; reads public job pages only)"
    min_interval_seconds: float = 10.0
    max_requests_per_run: int = 20
    timeout_seconds: float = 20.0
    offline: bool = False


class LLMConfig(_Strict):
    enabled: bool = False
    base_url: str = "http://localhost:1234/v1"
    model: str = ""
    timeout_seconds: float = 120.0


class GmailConfig(_Strict):
    client_secret_file: str = "client_secret.json"
    token_file: str = "token.json"
    window_days: int = 7


class PathsConfig(_Strict):
    db: str = "jac.sqlite"
    out: str = "out"
    cache: str = "cache"


class PdfConfig(_Strict):
    browser_path: str = ""


class Config(_Strict):
    mail: MailConfig = Field(default_factory=MailConfig)
    fetch: FetchConfig = Field(default_factory=FetchConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    gmail: GmailConfig = Field(default_factory=GmailConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    pdf: PdfConfig = Field(default_factory=PdfConfig)


def load_config(path: Path) -> Config:
    """Load config.yaml; a missing file means defaults."""
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config.model_validate(data)


def dump_config(cfg: Config, path: Path) -> None:
    """Write config.yaml with only the settings that differ from the defaults."""
    data = cfg.model_dump(mode="json", exclude_defaults=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
