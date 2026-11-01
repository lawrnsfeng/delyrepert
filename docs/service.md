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

## Running as systemd user units

`daily-report service` manages three systemd **user** units on any systemd Linux
host (a server or WSL). No root is needed.

| Unit | Role |
|---|---|
| `daily-report.timer` | `OnCalendar=<schedule>`, `Persistent=true` (catches up a missed run) |
| `daily-report-run.service` | oneshot: `daily-report run --scheduled` |
| `daily-report-serve.service` | `Restart=on-failure`: `daily-report serve` |

Each unit runs the absolute `daily-report` with `--config <abs>` and loads
`EnvironmentFile=<secrets file>`.

### Prerequisites

- **Linger on**, so user units survive logout and reboot: `loginctl enable-linger <user>`.
  Without it `service install` exits 5 and prints that command.
- **WSL**: enable systemd in `/etc/wsl.conf` (`[boot]` → `systemd=true`), then `wsl --shutdown`.
- Config keys `schedule` (an `OnCalendar=` expression, checked with
  `systemd-analyze calendar`; a bad one exits 2) and `secrets_file`
  (or `--secrets-file` / `DAILY_REPORT_SECRETS_FILE`).

### Steps

1. `daily-report --dry-run service install --config <file>` shows the units and the
   `systemctl --user link` / `enable --now` calls without touching anything.
2. `daily-report service install --config <file>` writes the units to
   `<state_dir>/systemd/`, links them and enables the timer and the listener.
3. `service stop` / `service start` act on the timer and the listener; installed
   units are not rewritten.
4. `service status [--json]` (same as `status`) adds `timer_next` and `serve_state`
   (`active|inactive|failed`) to the run status.
