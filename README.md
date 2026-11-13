# T2-BB8 — Trading212 Instrument Harvester

Harvests every instrument Trading212 offers — its Trading212 ticker, market ticker symbol, name,
ISIN, type, currency, exchange and every other attribute the API returns — plus the exchange
list, and stores them under `./data`.

Sibling of [T2-D2](https://github.com/samlarsen1/t2-d2), with the same guardrails.

---

## Architecture

```
t2-bb8/
├── src/t2bb8/
│   ├── cli.py       # `t2bb8-harvest` entrypoint, exit codes
│   ├── config.py    # env var settings (T212_*)
│   ├── client.py    # Trading212 metadata client, retries + rate-limit handling
│   ├── models.py    # pydantic models (extra attributes are kept)
│   ├── harvest.py   # delete previous outputs → fetch → validate → join exchanges → write
│   └── storage.py   # atomic JSON/text writers
├── tests/           # mocked-API tests
└── data/            # output (committed to git)
```

Each run calls two Trading212 endpoints:

| Endpoint | Rate limit | Gives |
|----------|-----------|-------|
| `GET /api/v0/equity/metadata/instruments` | 1 request / 50s | Every tradeable instrument |
| `GET /api/v0/equity/metadata/exchanges` | 1 request / 30s | Exchanges and their working schedules |

Instruments are linked to exchanges through `workingScheduleId`.

---

## Quick Start

### Prerequisites
- Python 3.12+
- [uv](https://github.com/astral-sh/uv) package manager
- Trading212 API key and secret with the **Metadata** permission (live or demo)

### Local Setup

```bash
# Install
uv sync

# Copy and fill in secrets (or symlink the same .env T2-D2 uses)
cp .env.example .env

# Harvest into ./data
uv run t2bb8-harvest

# Harvest somewhere else
uv run t2bb8-harvest --data-dir /tmp/t212-data
```

A run takes a few seconds unless Trading212 rate-limits it; on HTTP 429 the client waits for the
time the server asks plus 1s (at least the normal backoff, at most 120s) and retries, up to 4
attempts. Timeouts, network errors and
5xx responses are retried with exponential backoff.

### Output

Each run first deletes the previous run's files from the data directory, then writes:

| File | Contents |
|------|----------|
| `data/instruments.json` | One object per instrument: every API attribute (original camelCase keys) plus `exchangeId` and `exchangeName`, sorted by `ticker` |
| `data/exchanges.json` | Each exchange's `id`, `name`, `workingScheduleIds` and `instrumentCount` |
| `data/manifest.json` | Run time, base URL, counts (including skipped or unmatched instruments) and instrument types |
| `data/last-run.txt` | UTC timestamp of the last successful run, e.g. `2026-11-13T20:57:10.123456+00:00` |

Ticker fields:

| Field | Example | Meaning |
|-------|---------|---------|
| `ticker` | `AAPL_US_EQ`, `VODl_EQ` | Trading212's ticker, used in all other Trading212 API calls |
| `shortName` | `AAPL`, `VOD` | The instrument's ticker symbol on its exchange |

Only files with the four names above are deleted, so anything else in `--data-dir` is left alone.
Previous files are deleted before the API is called, so a failed run leaves no output files;
re-run it once the problem is fixed. `last-run.txt` is written last, so it only exists after a
run that fully succeeded. Each file is written atomically, so an interrupted write never leaves a
half-written file.

Instruments that fail validation (for example, no `ticker`) are skipped, logged as
`instrument_invalid`, and counted in `manifest.json`.

The `data/` folder is committed, so each harvest shows up as a diff. To record a new harvest:

```bash
uv run t2bb8-harvest
git add data/
git commit -m "Update instrument data"
```

Exit codes: `0` success, `2` configuration error, `3` Trading212 API error, `4` invalid payload or
write failure.

### Development

```bash
# Install and enable pre-commit hooks (run once after cloning)
uv sync
.venv/bin/pre-commit install

# Hooks run automatically on commit: detect-secrets, ruff, bandit, semgrep, pip-audit, mypy, pytest,
# plus a branch name check (see Branch naming below)
# To run manually against all files:
.venv/bin/pre-commit run --all-files
```

### Tests

```bash
.venv/bin/pytest -v
```

All Trading212 calls are mocked with realistic payloads, including timeouts, 5xx, 429 and auth
errors. Tests never read `.env`.

### Linters

```bash
.venv/bin/ruff check .
.venv/bin/mypy src/ tests/
```

#### Branch naming

Commit on a branch named after the type of change, in lowercase with hyphens:

| Pattern | Use for |
|---------|---------|
| `feature/<name>` | New behaviour |
| `bug/<name>` | Fixes |
| `task/<name>` | Everything else: config, docs, dependencies, CI |
| `hotfix/<name>` | Urgent fixes |
| `dependabot/...` | Created by Dependabot |

```bash
git switch -c feature/instrument-harvest
```

The `branch-name` hook rejects commits on any other branch, including `main`. The `Branch Name`
GitHub workflow checks pull requests the same way.

#### Vulnerability scanning

These hooks block the commit on any finding:

| Hook | Checks | Runs when |
|------|--------|-----------|
| `bandit` | Insecure Python patterns, medium severity and above (config in `pyproject.toml`) | Python files change |
| `semgrep` | Semgrep's OWASP Top 10 and Python rulesets, fetched from its registry (needs network) | Any file changes |
| `pip-audit` | Every package in `uv.lock` against known advisories (needs network) | Every commit |

```bash
# Run one scanner on its own
.venv/bin/pre-commit run bandit --all-files
.venv/bin/pre-commit run semgrep --all-files
.venv/bin/pre-commit run pip-audit --all-files

# Fix a vulnerable dependency, then re-run pip-audit
uv lock --upgrade-package <package>
uv sync
```

`detect-secrets` also scans `data/`, but skips lines that are exactly an ISIN field: some ISINs
(e.g. `CA03464C2058`) are all hex characters and would otherwise be flagged as keys.

The [Dependency Audit](.github/workflows/dependency-audit.yml) workflow also runs `pip-audit` daily.
`uv` ignores package versions published in the last 7 days (`exclude-newer` in `pyproject.toml`);
if a fix is too new or you're offline, commit with `SKIP=pip-audit git commit` and upgrade later.
Dependabot keeps GitHub Actions pinned to commit SHAs (`.github/dependabot.yml`).

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `T212_API_KEY` | Trading212 API key (needs the Metadata permission) |
| `T212_API_SECRET` | Trading212 API secret |
| `T212_BASE_URL` | `https://live.trading212.com` (default) or `https://demo.trading212.com`; any other host is rejected |
| `LOG_LEVEL` | `DEBUG`, `INFO` (default), `WARNING` or `ERROR` |

`.env` is read from the directory you run the command in; variables already set in the shell win.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `config_error` … `Missing required environment variables` | Create `.env` from `.env.example`, or export the variables |
| `t212_api_error` with `status=401` / `403` | Check the key and secret, and that the key has the Metadata permission. Demo keys only work with the demo URL |
| `t212_api_error` with `status=429` | Another process is using the same key; wait a minute and re-run |
| `t212_payload_invalid` | Trading212 changed the exchanges response; update `models.py` (run with `LOG_LEVEL=DEBUG` for detail) |
| Many `instrument_invalid` warnings | The warnings list the failing fields; update `models.py` to match |
| No files in `data/` after a run | The run failed after deleting the previous files; check the log, fix the cause and re-run |
