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
| Slack user token | `SLACK_USER_TOKEN` (see [docs/slack.md](docs/slack.md)) |
| secrets file | `--secrets-file`, then `DAILY_REPORT_SECRETS_FILE`, then `secrets_file` in the config |
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
| `slack.channel` | Channel ID `post` writes to (`C…`). |
| `slack.parent_match` | `user_id` and/or `text_contains` that spot today's standup parent message. |
| `post.target` | Default `thread`; or `thread_broadcast`, `channel`. |
| `context.commands` | Optional list of `{name, argv}` programs that print context JSON (see below). |
| `approval_cutoff` | `HH:MM` local time (default `12:00`) after which a pending draft cannot be approved. |
| `secrets_file` | The 0600 env file holding the four secrets, see [docs/service.md](docs/service.md). |

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
                     [--context PATH]... [--style NAME] [--out DIR] [--cook] [--dry-run]
```

- Takes the report from `--report` (a file, or `-` for stdin), or collects it
  now, exactly as `collect` does (same exit codes 2–4).
- Adds every pending note and every context item, then saves the draft to
  `<state_dir>/drafts/<run-id>.json` (run id `YYYYMMDD-HHMMSS`).
- Prints `draft <run-id>`, a plain-text preview, then one Block Kit Builder link per
  style (labelled `Style n/7 — name` while all seven show). With
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
- In a terminal it then prompts `[p]ost [e]dit [s]tyle [t]arget [c]ancel`
  (see `preview`). With no terminal it prints
  `post with: daily-report post <run-id> --style <style> --yes` and exits 0;
  nothing is posted.
- `--cook` runs `cook.command` (see below) before rendering.

## `preview`, `draft edit` and `--cook`

```bash
daily-report preview RUN_ID [--gallery] [--config PATH] [--state-dir PATH]
daily-report draft edit RUN_ID --from FILE [--config PATH] [--state-dir PATH]
```

- `preview` shows a saved draft and runs the same prompt loop as `compose`.
  While `render.style` is unset it shows all seven styles; `--gallery` shows
  them again later. Keys: `p` posts (asks for a style number if none is set,
  then offers to save it as `render.style`, the only key daily-report ever
  writes to your config); `e` opens `$VISUAL`/`$EDITOR` on a YAML file of the
  editable fields; `s` picks a style; `t` cycles `thread`,
  `thread_broadcast`, `channel`; `c` cancels and posts nothing. With no
  `$VISUAL`/`$EDITOR` it prints `edit <state_dir>/drafts/<run-id>.edit.yaml
  then press Enter`. If no standup thread exists today it offers a top-level
  post instead.
- The edit YAML is `{headline, items: [{id, summary, hidden}], notes: [{text,
  group}]}`. Summaries stay within 120 characters and ids cannot change; a
  broken edit reopens the editor with `# error: …` on top. Under `--dry-run`
  the file lives in a system temp dir.
- `draft edit RUN_ID --from FILE` applies such a YAML to the saved draft (exit 2
  naming the field if it is invalid; the draft is untouched). The Claude Code
  skill in `skill/` uses it.
- `--cook` sets `cook.command` to polish wording only: it gets
  `{headline, items[{id, kind, title, summary}]}` on stdin (no notes) and must
  print `{headline, summaries: {id: text}}` (or the same under
  `structured_output`) within `cook.timeout_s`. Anything else (a missing or
  extra id, a summary over 120 characters, an extra field, bad JSON, a non-zero
  exit, a timeout) is rejected: the script text stays and the preview shows
  `cook rejected: <cause>`. It runs only with `--cook`, also under `--dry-run`.

```toml
[cook]
command = ["claude", "-p", "--output-format", "json", "--json-schema", "<schema>"]
timeout_s = 60
```

## Claude Code skill

Link `skill/` into your Claude Code skills directory so the `daily-report`
skill is available. It runs collect, optional context, notes, compose, cook,
preview and edit, and posts only after you say "post".

## `post`

```bash
daily-report post RUN_ID [--style NAME] [--target thread|thread_broadcast|channel] [--yes]
```

Posts the draft `compose` saved as `RUN_ID`, as you (the user token), exactly
once. `thread` replies to today's standup parent (the latest message since
local midnight that matches `slack.parent_match`); `thread_broadcast` also
shows the reply in the channel; `channel` posts top-level. No parent today
exits 10 and suggests `--target channel`. A blocker line uses
`emoji.blocked_animated` only if `emoji.list` has it, else ⛔. After Slack
answers `ok`, the draft records `posted` and the notes inbox drops the notes
the post carried. A second `post` of the same draft exits 8. Without `--yes`
it shows the target and asks first. Under `--dry-run` it still calls
`auth.test`, `conversations.history` and `emoji.list`, prints
`target: …` and the exact request body on stdout, and sends nothing.

## `note`

```bash
daily-report note "Out 15:00–17:00 (dentist)" [--group attention|today|done]
daily-report note list
daily-report note rm 1
```

Notes wait in `<state_dir>/notes/pending.jsonl` (one `{at, text, group}` per
line, text up to 120 characters) and show as 📝 Note in the next standup.
`list` and `rm N` are reserved words, so a note cannot be exactly `list`.

## `demo`

```bash
daily-report demo --out ./demo
```

Runs the whole flow from built-in mock Jira fixtures (no Jira, no config, no
state dir needed): an in-process fake Jira, a seeded yesterday snapshot, one
note and one other-skill context item, with the clock frozen at
`--as-of 2026-11-03T09:00:00+00:00`. It writes one Block Kit payload per style
(`bluf`, `ytb`, `table`, `markdown`, `compact`, `cards`, `richlist`) to `--out`
and prints a Block Kit Builder link for each. State goes to a fresh temp dir
(printed as `state dir: …`) unless `--state-dir`, `DAILY_REPORT_DEMO_STATE_DIR`
or `demo.state_dir` names one. Under `--dry-run` nothing is written but `--out`:
the seeded snapshot is read in place and the snapshot write is only reported.

Flags: `--style NAME` (one style), `--fixtures-dir` (or
`DAILY_REPORT_DEMO_FIXTURES`; layout `jira/`, `state/snapshots/`,
`notes/pending.jsonl`, `context/*.json`), `--config`, `--cook` (polish the
wording with `cook.command`; a rejected answer keeps the script text) and
`--broadcast`.

With `DAILY_REPORT_DEMO_SLACK_USER_TOKEN` set, the demo also posts into your
test workspace. It first calls `auth.test` and refuses (exit 5) unless the
token's team is `demo.slack_team_id`. After you answer `y` to
`Post 7 replies to #<channel>?` it posts the parent
`🗓️ Daily standup — 2026-11-03 (demo)` to `demo.channel`, finds it again with
the real parent finder (text match on `demo.parent_text`, default `(demo)`) and
adds one reply per style headed `Style n/7 — <name>`. `--broadcast` also shows
every reply in the channel. `slack.api_url` from the config is used when set.

```toml
[demo]
slack_team_id = "T0123456789"   # the test workspace's team ID
channel = "C0123456789"         # channel the demo posts into
# parent_text = "(demo)"        # text the finder matches on
# state_dir = "/path/to/demo-state"

[cook]                          # only used by --cook
command = ["my-cook", "--json"]
timeout_s = 60
```

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

## Standup service: `run`, `approve`, `pause`, `resume`, `status`

`run [--scheduled]` collects and composes, then keeps the draft *pending*
(`runs/<id>.json`) until `approve <id> [--style] [--target]` posts it. A draft
expires at `approval_cutoff` or at the next run. `pause [--until YYYY-MM-DD]`
makes scheduled runs exit 0 with `skipped: paused`; `resume` lifts it;
`status [--json]` shows both. `run` and `approve` need the secrets file. See
[docs/service.md](docs/service.md).

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
