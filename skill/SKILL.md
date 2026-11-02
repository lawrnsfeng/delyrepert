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

1. **Run.** For the owner's own report run `daily-report self`; for the team report run
   `daily-report standup --format text`. Use no terminal prompts. If it exits non-zero, show the
   message and stop (exit 3/4 is Jira, 2 is config).
2. **Context (optional).** Ask: "Add context from another skill?" If yes, run that skill so it
   writes a context JSON file, and rerun with `--context <file>` (repeatable).
3. **Notes.** Run `daily-report note list` and show the pending notes. Offer to add one with
   `daily-report note "<text>" --group attention|today|done`; rerun to include it.
4. **Show it.** Print the output to the owner to copy and paste into Slack. Offer `--copy` (puts
   the text on the clipboard) and `--format html --out <file>` for a rich paste that keeps bold
   and links (open the file in a browser, Select all, Copy, paste into Slack). Ask whether to
   cook: if yes, rerun with `--cook` (needs `cook.command`); if stderr shows
   `cook rejected: <cause>`, tell the owner the original wording was kept.
5. **Post (only if Slack is set up).** `daily-report standup` with `slack.channel` and
   `SLACK_USER_TOKEN` previews the draft. Only after the owner says "post", run
   `daily-report post <run-id> --style <style> --yes` (the run id is on the `draft <run-id>` line).
   If it exits 10 (no standup thread today), ask whether to post top-level and, if yes, rerun with
   `--target channel`. Show the permalink it prints. Otherwise, if the owner says "cancel", stop.

`daily-report self --dm` also DMs the report to the owner (needs `SLACK_BOT_TOKEN`); it is not a
post to the team.
