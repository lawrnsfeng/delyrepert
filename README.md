# daily-report

Collects your Jira progress since the last run and turns it into your daily
standup. No AI is involved: `collect` diffs your Jira tickets against the
previous snapshot, and `compose` turns that report, your notes and other
tools' context into a Slack Block Kit draft in seven styles.

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
| `state_dir` | Where snapshots, notes and drafts are kept (`snapshots/`, `notes/pending.jsonl`, `drafts/`). |
| `timezone` | IANA zone for "today", snapshot names and working days; the system zone when unset. |
| `jira.base_url` | Your Jira Cloud site, e.g. `https://your-site.atlassian.net`. |
| `jira.email` | The account email the API token belongs to. |
| `jira.blocked_statuses` | Statuses that make a ticket `blocked` (default `["Blocked"]`, case-insensitive). |
| `jira.flagged_field` | Field whose `flagged_value` also makes a ticket `blocked` (default `"Flagged"`; `""` disables). |
| `jira.flagged_value` | Default `"Impediment"`. |
| `display_name` | Optional name shown before the standup headline. |
| `render.style` | Optional default style (`bluf`, `ytb`, `table`, `markdown`, `compact`, `cards`, `richlist`). Unset: every preview shows all seven. |
| `emoji.blocked_animated` | Optional animated custom emoji name (no colons) for blocker lines, used only when Slack lists it. |
| `context.commands` | Optional list of `{name, argv}` programs that print context JSON (see below). |

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

## `compose`

```bash
daily-report compose [--config PATH] [--state-dir PATH] [--as-of ISO-8601] [--report PATH|-]
                     [--context PATH]... [--style NAME] [--out DIR] [--dry-run]
```

- Takes the report from `--report` (a file, or `-` for stdin), or collects it
  now, exactly as `collect` does (same exit codes 2–4).
- Adds every pending note and every context item, then saves the draft to
  `<state_dir>/drafts/<run-id>.json` (run id `YYYYMMDD-HHMMSS`).
- Prints `draft <run-id>`, then one Block Kit Builder link per style. With
  `--out DIR` it also writes `<style>.json` (the `chat.postMessage` body) per
  style.
- Renders `--style`, else `render.style`, else all seven styles:
  `bluf` (Needs attention / Since / Today), `ytb` (Yesterday / Today /
  Blockers), `table`, `markdown`, `compact`, `cards` and `richlist`.
- Every line starts with an icon and a word: ⛔ Blocked, ⚠️ At risk,
  💬 New comments, 💬 Replied, ✅ Done, ↪️ Handed off, ▶️ In progress,
  📝 Note.
- If a style breaks a Slack limit (50 blocks, 3000 characters per section,
  100 table rows), done items collapse to bare keys; if it still does not
  fit, that style renders as `compact` and the draft's `render_notice` says
  why. Report warnings, dropped context and the notice go to stderr only.

## `note`

```bash
daily-report note "Out 15:00–17:00 (dentist)" [--group attention|today|done]
daily-report note list
daily-report note rm 1
```

Notes wait in `<state_dir>/notes/pending.jsonl` (one `{at, text, group}` per
line, text up to 120 characters) and show as 📝 Note in the next standup.
`list` and `rm N` are reserved words, so a note cannot be exactly `list`.

## Context from other tools

`compose --context FILE` (repeatable) and each `context.commands` program
supply JSON like:

```json
{"source": "my-skill", "items": [
  {"group": "today", "text": "Rehearse the demo", "link": "https://…", "priority": "normal"}
]}
```

`group` is `attention`, `done`, `today` or `note`; `text` is up to 120
characters; `link` and `priority` (`blocked`, `at_risk`, `normal`) are
optional. An invalid item, invalid JSON or a failing command is dropped with a
reason in the draft's `dropped` list; it never stops the draft. The footer
names every source.

## Dry-run

`--dry-run` works on every command, before or after the command name, and so
does `DAILY_REPORT_DRY_RUN` (`1`, `true`, `yes` turn it on; `0`, `false`, `no`
or empty leave it off; anything else exits 2). Dry-run reads Jira as usual but
performs no side effect: each one is reported on stderr as
`[dry-run] would <verb> <target>`. Stdout and exit codes are unchanged. An
explicit `--out` is still written, since it is the debug output you asked for.
Under dry-run `compose` saves no draft and `note` leaves the inbox untouched.

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
