# Standup service

<!-- section: run, approve, pause (step 6) -->
## Secrets

Four variables live in one env file, mode 0600 (`chmod 600`):

```
SLACK_BOT_TOKEN=...
SLACK_APP_TOKEN=...
SLACK_USER_TOKEN=...
JIRA_API_TOKEN=...
```

Pass it with `--secrets-file`, `DAILY_REPORT_SECRETS_FILE` or `secrets_file` in the
config (no source: exit 2). `run` and `approve` refuse a file readable by group or
others, an unreadable file or a missing variable (exit 6, naming only the variable).

## Verbs

| Command | Does |
|---|---|
| `run [--scheduled]` | Take `run.lock`, collect, compose, leave `runs/<id>.json` `pending`. Exit 7 if a run holds the lock. A scheduled run exits 0 with `skipped: paused` while paused. |
| `approve <id> [--style] [--target]` | Post the pending draft through `post`'s service. Exit 8 if the run is not pending or past `approval_cutoff`; 9 and 10 as for `post`. A failed approve stays pending. |
| `pause [--until YYYY-MM-DD]` | Write `paused.json`; scheduled runs skip until `resume` or the `--until` day. Manual runs still work. |
| `resume` | Delete `paused.json`. |
| `status [--json]` | `{paused{until}\|null, last_run{id,status}\|null}`. |

A run's status is `running`, `pending`, `posted`, `skipped`, `expired`, `failed` or
`paused_skip`. The next run expires older pending drafts.

Every verb honours `--dry-run`: effects print as `[dry-run] would …` and nothing is
written or posted. Exit codes are in the README.
<!-- end section -->
