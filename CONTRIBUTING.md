# Contributing

Keep public documentation focused on the project: installation and deployment belong in
[SETUP.md](SETUP.md), architecture and model contracts in `docs/`, and the overview in
[README.md](README.md).

## Local working notes

Write all temporary session summaries, agent reports, QA execution reports, reconciliation
notes, and personal handoff checklists directly into **`.session-notes/`**. This convention
applies to both human contributors and automated coding tools. Preserve useful notes locally,
but do not write them at the repository root or commit them under another directory.
Promote reusable instructions into the public documentation in project-focused language.

`.session-notes/` is excluded from Git and Docker build contexts. Root-level report patterns
are also ignored to prevent accidental commits. Never force-add ignored notes, credentials,
wearable data, generated databases, or runtime caches. Review `git diff --cached --stat`
before committing; `.gitignore` does not remove files that are already tracked.

## Verification and data safety

Install `.[dev]` and run `ruff check .`, `ruff format --check .`, `mypy`, and `pytest`.
Tests use fixtures and temporary databases. Keep live WHOOP/Postgres opt-ins disabled during
development and never use real credentials to make an offline test pass.

Do not include private data in notebook outputs, logs, screenshots, or test fixtures. Removing
a file from the current tree does not remove it from Git history. Any history rewrite or
force-push requires a separate, deliberate maintenance decision.
