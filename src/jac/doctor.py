"""`jac doctor`: check the setup and say in plain words what to fix. Read-only everywhere."""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

import requests
from pydantic import ValidationError

from jac.config import IMAP_PRESETS, Config, ConfigError, load_config
from jac.paths import Locations
from jac.profile import load_profile
from jac.profile_check import check_profile
from jac.tailor.pdf import find_browser

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def _probe_llm(base_url: str) -> int:
    """GET <base_url>/models and return the HTTP status. Only called when an LLM is configured."""
    return requests.get(f"{base_url.rstrip('/')}/models", timeout=5).status_code


def _check_mail(loc: Locations, cfg: Config, imap_count: Callable[[Config], int]) -> list[Check]:
    mail = cfg.mail
    checks: list[Check] = []
    if mail.authserv_ids():
        checks.append(Check("trusted authserv-id", OK, ", ".join(mail.authserv_ids())))
    else:
        checks.append(Check("trusted authserv-id", FAIL, "not set; mails cannot be verified",
                            "Set mail.provider (gmail, outlook, yahoo, fastmail) in config.yaml, or list trusted_authserv_ids."))
    if mail.method == "eml":
        folder = loc.resolve(mail.eml_folder)
        if folder.is_dir():
            count = len(list(folder.glob("*.eml")))
            status = OK if count else WARN
            checks.append(Check("mail folder", status, f"{folder} ({count} .eml files)",
                                "" if count else "Save your LinkedIn alert mails as .eml files into this folder."))
        else:
            checks.append(Check("mail folder", FAIL, f"{folder} does not exist", "Create it and save .eml files there."))
    elif mail.method == "imap":
        if not mail.imap.user or not (mail.imap.host or mail.provider in IMAP_PRESETS):
            checks.append(Check("IMAP", FAIL, "host or user missing", "Run `jac init` again or edit mail.imap in config.yaml."))
        else:
            try:
                total = imap_count(cfg)
                checks.append(Check("IMAP (read-only)", OK, f"logged in, folder {mail.imap.folder} has {total} messages"))
            except Exception as exc:  # network and auth errors vary widely; report the type only
                checks.append(Check("IMAP (read-only)", FAIL, f"could not connect: {type(exc).__name__}",
                                    "Check host, user and the app password (not your normal password); IMAP must be enabled."))
    else:
        secret = loc.resolve(cfg.gmail.client_secret_file)
        status = OK if secret.exists() else FAIL
        checks.append(Check("Gmail API client file", status, str(secret),
                            "" if secret.exists() else "Download your OAuth client file from Google Cloud and save it there."))
    return checks


def run_doctor(loc: Locations, imap_count: Callable[[Config], int] | None = None,
               probe_llm: Callable[[str], int] = _probe_llm) -> list[Check]:
    """Run every check and return the results."""
    checks = [Check("Python", OK if sys.version_info >= (3, 10) else FAIL, sys.version.split()[0],
                    "" if sys.version_info >= (3, 10) else "Install Python 3.10 or newer.")]
    try:
        cfg = load_config(loc.config_file)
        checks.append(Check("config", OK if loc.config_file.exists() else WARN,
                            str(loc.config_file) if loc.config_file.exists() else "no config.yaml, defaults in use",
                            "" if loc.config_file.exists() else "Run `jac init`."))
    except (ValidationError, ValueError) as exc:
        return checks + [Check("config", FAIL, f"{loc.config_file} is invalid: {type(exc).__name__}", "Fix or delete it, then run `jac init`.")]
    if loc.profile_file.exists():
        try:
            profile = load_profile(loc.profile_file)
            _, missing = check_profile(profile)
            checks.append(Check("profile", OK if not missing else WARN, f"{len(profile.facts)} facts, {len(missing)} gaps",
                                "Run `jac profile check` to see the gaps." if missing else ""))
        except (ValidationError, ValueError) as exc:
            checks.append(Check("profile", FAIL, f"invalid: {type(exc).__name__}", "Run `jac profile check` after fixing profile.yaml."))
    else:
        checks.append(Check("profile", FAIL, f"{loc.profile_file} not found", "Run `jac init`, then `jac import-cv your_cv.pdf`."))
    if imap_count is None:
        imap_count = _default_imap_count
    checks += _check_mail(loc, cfg, imap_count)
    browser = find_browser(cfg.pdf.browser_path)
    checks.append(Check("browser for PDF", OK if browser else WARN, str(browser) if browser else "none found",
                        "" if browser else "Install Chrome, Edge or Chromium for PDF output; HTML CVs work without it."))
    if cfg.llm.enabled:
        try:
            status = probe_llm(cfg.llm.base_url)
            checks.append(Check("local LLM", OK if status == 200 else WARN, f"{cfg.llm.base_url} answered HTTP {status}",
                                "" if status == 200 else "Start your LLM server or check llm.base_url."))
        except requests.RequestException:
            checks.append(Check("local LLM", WARN, f"{cfg.llm.base_url} not reachable", "Start the server, or set llm.enabled to false; the keyword mode needs no LLM."))
    else:
        checks.append(Check("local LLM", OK, "not enabled (optional)"))
    return checks


def _default_imap_count(cfg: Config) -> int:
    from jac.imap import ImapReader
    from jac.keyring_store import password_for
    from jac.sources import LINKEDIN

    imap = cfg.mail.imap.model_copy()
    if not imap.host:
        imap.host, imap.port = IMAP_PRESETS[cfg.mail.provider]
    if not imap.user:
        raise ConfigError("mail.imap.user is not set")
    return ImapReader(imap, LINKEDIN.sender_domain, lambda: password_for(imap.user)).check()


def format_report(checks: list[Check]) -> str:
    """Plain-text report: one line per check, then what to fix."""
    lines = [f"[{c.status.upper():4}] {c.name}: {c.detail}" for c in checks]
    fixes = [f"- {c.name}: {c.fix}" for c in checks if c.fix]
    if fixes:
        lines += ["", "What to fix:"] + fixes
    else:
        lines += ["", "Everything looks fine."]
    return "\n".join(lines)
