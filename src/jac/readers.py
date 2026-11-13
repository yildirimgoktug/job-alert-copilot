"""One interface for the three mail sources: a folder of .eml files, IMAP, or the Gmail API."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from jac.config import IMAP_PRESETS, Config, ConfigError
from jac.ingest import RawMail, read_eml_folder, read_gmail
from jac.sources import Source


def build_reader(cfg: Config, source: Source, config_dir: Path, method: str | None = None,
                 eml_dir: Path | None = None) -> Iterable[RawMail]:
    """Return an iterable of raw mails for the chosen method (default: config.mail.method)."""
    method = method or cfg.mail.method
    senders = cfg.mail.allowed_senders or list(source.allowed_senders)
    if method == "eml":
        folder = eml_dir or (config_dir / cfg.mail.eml_folder)
        if not folder.is_dir():
            raise ConfigError(f"mail folder {folder} does not exist; put .eml files there or use --eml-dir")
        return read_eml_folder(folder)
    if method == "gmail":
        return read_gmail(senders, cfg.gmail, config_dir)
    if method == "imap":
        from jac.imap import ImapReader
        from jac.keyring_store import password_for

        imap = cfg.mail.imap.model_copy()
        if not imap.host and cfg.mail.provider in IMAP_PRESETS:
            imap.host, imap.port = IMAP_PRESETS[cfg.mail.provider]
        if not imap.host or not imap.user:
            raise ConfigError("mail.imap.host and mail.imap.user are not set; run `jac init` or edit config.yaml")
        return ImapReader(imap, source.sender_domain, lambda: password_for(imap.user)).read()
    raise ConfigError(f"unknown mail method {method!r}")
