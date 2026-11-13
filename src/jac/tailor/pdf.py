"""PDF conversion with a locally installed headless Chrome or Edge, and page counting.

If no browser is found the HTML is kept and page fitting falls back to an estimate. The browser
is started with --disable-gpu and only loads a local file.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_browser(configured: str = "") -> Path | None:
    """The configured browser path, else the first installed Chrome/Edge, else None."""
    if configured:
        path = Path(configured)
        return path if path.exists() else None
    for candidate in _CANDIDATES:
        if Path(candidate).exists():
            return Path(candidate)
    for name in ("google-chrome", "chromium", "msedge", "chrome"):
        if found := shutil.which(name):
            return Path(found)
    return None


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """Number of page objects in a PDF (counts '/Type /Page', not '/Type /Pages')."""
    return len(re.findall(rb"/Type\s*/Page(?![A-Za-z])", pdf_bytes))


def html_to_pdf(browser: Path, html_file: Path, pdf_file: Path, timeout: float = 60.0) -> bool:
    """Print a local HTML file to PDF. Returns False if the browser fails."""
    with tempfile.TemporaryDirectory() as profile_dir:
        command = [
            str(browser),
            "--headless=new",
            "--disable-gpu",
            "--no-pdf-header-footer",
            f"--user-data-dir={profile_dir}",
            f"--print-to-pdf={pdf_file}",
            html_file.resolve().as_uri(),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=timeout)
        except (subprocess.SubprocessError, OSError):
            return False
    return pdf_file.exists()


class BrowserPageCounter:
    """Counts the pages of an HTML document by printing it to PDF with a real browser."""

    def __init__(self, browser: Path) -> None:
        self._browser = browser

    def count(self, html: str) -> int | None:
        with tempfile.TemporaryDirectory() as folder:
            html_file, pdf_file = Path(folder) / "cv.html", Path(folder) / "cv.pdf"
            html_file.write_text(html, encoding="utf-8")
            if not html_to_pdf(self._browser, html_file, pdf_file):
                return None
            return count_pdf_pages(pdf_file.read_bytes())
