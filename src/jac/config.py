"""Tool configuration (config.yaml). Everything has a safe default; the file is optional."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SENDERS = [
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MailConfig(_Strict):
    """Which messages are even considered, and whose Authentication-Results we trust."""

    allowed_senders: list[str] = Field(default_factory=lambda: list(DEFAULT_SENDERS))
    trusted_authserv_ids: list[str] = Field(default_factory=lambda: ["mx.google.com"])
    sender_domain: str = "linkedin.com"


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
    profile: str = "profile.yaml"
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
