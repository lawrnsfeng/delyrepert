# daily-report

Collects your Jira progress since the last run and turns it into the items of
your daily standup. No AI is involved: `collect` diffs your Jira tickets
against the previous snapshot and prints a structured report.

## Install

```bash
uv sync          # or: make sync
uv run daily-report --help
```

## Configure

daily-report reads one TOML file. Copy `config.example.toml` anywhere and point
the CLI at it. Nothing falls back to your home directory or the current
directory: a setting with no source is an error (exit 2).

| Setting | Resolution order |
|---|---|
| config file | `--config`, then `DAILY_REPORT_CONFIG` |
| state dir | `--state-dir`, then `DAILY_REPORT_STATE_DIR`, then `state_dir` in the config |
| Jira API token | `JIRA_API_TOKEN` |
| dry-run | `--dry-run` (before or after the command), or `DAILY_REPORT_DRY_RUN` |

Config keys:

| Key | Meaning |
|---|---|
| `state_dir` | Where snapshots are kept (`<state_dir>/snapshots/YYYY-MM-DD.json`). |
| `timezone` | IANA zone for "today", snapshot names and working days; the system zone when unset. |
| `jira.base_url` | Your Jira Cloud site, e.g. `https://your-site.atlassian.net`. |
| `jira.email` | The account email the API token belongs to. |
| `jira.blocked_statuses` | Statuses that make a ticket `blocked` (default `["Blocked"]`, case-insensitive). |
| `jira.flagged_field` | Field whose `flagged_value` also makes a ticket `blocked` (default `"Flagged"`; `""` disables). |
| `jira.flagged_value` | Default `"Impediment"`. |

## `collect`

```bash
daily-report collect [--config PATH] [--state-dir PATH] [--as-of ISO-8601] [--out PATH] [--dry-run]
```

- Reads the tickets assigned to you (open ones, plus done ones updated since the baseline).
- Diffs them against the newest snapshot dated before today, so Monday diffs
  against Friday. The first run covers the last 24 hours.
- Prints the `Report` JSON on stdout (and to `--out` when given), then writes
  today's snapshot. A rerun on the same day gives the same items.
- Each item lands in one bucket, first match wins: `left_scope`, `blocked`,
  `done`, `at_risk` (due by the next working day), `in_progress`, `comment`.
  Items sort by bucket (blocked, at_risk, done, in_progress, comment,
  left_scope), then Jira priority, then key.
- If one ticket's changelog or comments cannot be fetched, the report is still
  printed with a `warnings` entry (also on stderr), and that ticket is retried
  on the next run.

## Dry-run

`--dry-run` works on every command, before or after the command name, and so
does `DAILY_REPORT_DRY_RUN` (`1`, `true`, `yes` turn it on; `0`, `false`, `no`
or empty leave it off; anything else exits 2). Dry-run reads Jira as usual but
performs no side effect: each one is reported on stderr as
`[dry-run] would <verb> <target>`. Stdout and exit codes are unchanged. An
explicit `--out` is still written, since it is the debug output you asked for.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok (a partial report is also 0) |
| 2 | config or usage error |
| 3 | Jira rejected credentials |
| 4 | Jira search failed |
| 5 | `demo`: workspace mismatch; `service install`: linger off |
| 6 | secrets file |
| 7 | run in progress |
| 8 | not pending or already posted |
| 9 | Slack API error |
| 10 | the owner must choose (no style, or no parent thread) |

## Development

```bash
make check   # ruff, mypy, ty, pytest with 100% branch coverage
make pre     # pre-commit hooks
```
