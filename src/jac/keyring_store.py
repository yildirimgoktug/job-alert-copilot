"""Mail passwords live only in the operating system keyring. Never in a file, never logged."""
from __future__ import annotations

import getpass
from collections.abc import Callable

import keyring

SERVICE = "job-alert-copilot"


def password_for(user: str, ask: Callable[[str], str] = getpass.getpass) -> str:
    """Return the stored app password for user, asking once and saving it if there is none."""
    stored = keyring.get_password(SERVICE, user)
    if stored:
        return stored
    entered = ask(f"App password for {user} (stored only in your OS keyring): ")
    if not entered:
        raise RuntimeError("no password entered")
    keyring.set_password(SERVICE, user, entered)
    return entered


def forget(user: str) -> None:
    """Remove the stored password, if any."""
    try:
        keyring.delete_password(SERVICE, user)
    except keyring.errors.PasswordDeleteError:
        pass
