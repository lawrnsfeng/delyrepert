---
name: daily-report
description: Build and post the daily standup report from my Jira progress. Use when the owner asks for the daily standup, a standup report, or to post standup to Slack.
---

# daily-report

Link this `skill/` folder into your Claude Code skills directory to use it. Every step calls only
`daily-report …` commands. Config and state come from `DAILY_REPORT_CONFIG` and
`DAILY_REPORT_STATE_DIR`; if either is unset, ask the owner and pass `--config` / `--state-dir`.

Never post unless the owner has explicitly said "post" after seeing the preview. "cancel" ends the
flow and posts nothing.

## Flow

1. **Collect.** Run `daily-report collect`. If it exits non-zero, show the message and stop.
2. **Context.** Ask: "Add context from another skill?" If yes, run that skill so it writes a
   context JSON file, and keep the path for `--context`.
3. **Notes.** Run `daily-report note list` and show the pending notes. Offer to add one with
   `daily-report note "<text>" --group attention|today|done`.
4. **Compose.** Run `daily-report compose --report <report.json> [--context <file>]... --style bluf --mode team`
   (`--mode self` for the owner's own detailed report) with no terminal attached. Read the `draft <run-id>` line and the preview it prints. Ask whether
   to cook: if yes, rerun compose with `--cook` (needs `cook.command` in the config). If the
   preview shows `cook rejected: <cause>`, tell the owner the script wording was kept.
5. **Preview and edit.** Show the preview. If the owner wants changes, write the edit YAML
   (`headline`, `items: [{id, summary, hidden}]`, `notes: [{text, group}]`; summaries at most 120
   characters; ids unchanged) and run `daily-report draft edit <run-id> --from <file>`. On exit 2
   fix the named field and retry. Run `daily-report preview <run-id>` again to show the result.
   If `render.style` is unset, show the seven styles and ask which one to use.
6. **Post.** Only after the owner says "post": run
   `daily-report post <run-id> --style <style> --yes`. If it exits 10 (no standup thread today),
   ask whether to post top-level and, if yes, rerun with `--target channel`. Show the permalink it
   prints. Otherwise, if the owner says "cancel", stop.
