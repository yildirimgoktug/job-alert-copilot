# Contributing

Thanks for helping. The product promise comes first: the tool prepares, a human applies.

## Rules that every change must keep

1. No code path applies, clicks, logs in to a job site, uses site cookies or sends e-mail. `tests/test_no_submit.py` greps the source; do not weaken it.
2. Mail is verified before anything is parsed. Do not add a way around `verify_message`.
3. The CV comes from the fact bank only. LLM output is validated; hard rules are never overridden by a model.
4. No personal data in the repo, tests or fixtures. Use fictional people and `example.com`.
5. Tests make no network calls and load no model. Use fixtures, `FakeLLM`, fake IMAP objects and mocked keyring.

## Setup

```bash
python -m venv .venv && . .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e ".[dev,gmail]"
pytest -q
```

## Adding a job site

Add a `Source` in `src/jac/sources.py` with its sender domain, allowed sender addresses, a strict `canonicalize` function and a posting URL rule, register it in `SOURCES`, and add tests for: an authentic mail, a spoofed display name, lookalike domains, tracking parameters, shorteners and userinfo tricks. Check the site's terms before adding any fetching.

## Style

Small modules, type hints, docstrings that say what and why, no dead code. Commits are small with a plain one-line message.
