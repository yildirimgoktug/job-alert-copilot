from pathlib import Path

import pytest

from jac.postings import Posting, parse_posting
from jac.profile import Profile, load_profile

ROOT = Path(__file__).resolve().parent.parent
POSTINGS = ROOT / "examples" / "postings"


def posting_html(job_id: str) -> str:
    return (POSTINGS / f"{job_id}.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def profile() -> Profile:
    return load_profile(ROOT / "profile.example.yaml")


@pytest.fixture
def load_posting():
    def _load(job_id: str) -> Posting:
        posting = parse_posting(posting_html(job_id), job_id)
        assert posting is not None, job_id
        return posting

    return _load
