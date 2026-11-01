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

<!-- section: Slack listener (step 8) -->
## Slack listener

`serve` runs a Bolt app over Socket Mode, so the machine needs no public URL.

1. Create the app from `slack/manifest.yaml` (api.slack.com/apps > From manifest). It
   asks for the bot scopes `commands`, `chat:write` and `im:write`, turns on Socket Mode and
   interactivity, and defines `/standup`.
2. Install it to the workspace; put the bot token in `SLACK_BOT_TOKEN`.
3. Create an app-level token with `connections:write`; put it in `SLACK_APP_TOKEN`.
4. Set `owner_slack_user_id` in the config (your member ID, `U…`).

Only that user is served: anyone else gets the ephemeral `not authorized` and nothing
changes. The bot token sends the DM and edits it; the user token (`SLACK_USER_TOKEN`)
still posts the standup as you.

| Verb | Does |
|---|---|
| `/standup run` | Same as `run`; the preview DM follows. `run <id> already in progress` while the lock is held. |
| `/standup preview` | DM the pending draft again (`no pending draft — /standup run` if none). |
| `/standup status` | Timer next run, listener state, pause state, last run. |
| `/standup pause [YYYY-MM-DD]` | Write `paused.json` with `by: slack`. |
| `/standup resume` | Delete it. |
| `/standup note <text>` | Add a note (120 characters, group `today`) for the next standup. |
| `/standup help` | List the verbs. |

The approval DM carries Post, Edit notes and Skip. While `render.style` is unset it shows
a gallery of all seven styles with a style select (preset `bluf`); the target select is
preset to `post.target`; Post uses what is selected. Edit notes opens a modal whose note
joins the inbox and the draft. After the cutoff, Post answers `draft <id> expired at …`.
Listener down: the draft stays pending and `approve` still works from the CLI. A failed
scheduled run DMs `❌ Run <id> failed: …`; if that DM fails it is only logged.

`serve --dry-run` resolves config and secrets, calls `auth.test`, prints the verbs and
action IDs and `[dry-run] would open Socket Mode as <bot user>`, then exits 0 without
connecting. `serve` logs JSON lines on stderr.
<!-- end section -->
