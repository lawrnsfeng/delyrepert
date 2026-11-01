# Slack setup for `daily-report post`

`post` writes as you, so it needs a **user token** (`xoxp-…`), not a bot token.
A bot token is refused (exit 2) when `auth.test` returns a `bot_id`.

## Create the token

1. Create a Slack app in your workspace (api.slack.com/apps → *Create New App*).
2. Under *OAuth & Permissions* add these **User Token Scopes**:

   | Scope | Used for |
   |---|---|
   | `chat:write` | `chat.postMessage` |
   | `channels:history` | finding today's standup parent in a public channel |
   | `groups:history` | the same, in a private channel |
   | `emoji:read` | checking the animated blocker emoji exists |

3. Install the app to the workspace and copy the *User OAuth Token*.
4. Export it where the CLI runs: `export SLACK_USER_TOKEN=xoxp-…`. The token is
   never read from the config file.

Without `emoji:read` the post still goes out: stderr says
`emoji:read missing; using ⛔`.

## Find the channel ID

Open the channel in Slack, click its name at the top, and scroll to the bottom
of the *About* tab: the ID starts with `C` (public) or `G` (private). Put it in
the config as `slack.channel`. The same ID goes in the `[slack]` table of
`config.example.toml`.

## Find the parent match

`slack.parent_match` picks today's standup parent message. Set `user_id` (the
author's member ID: profile → *⋮* → *Copy member ID*) and/or `text_contains`
(text of the parent, e.g. `Daily standup`). A message must satisfy every rule you
set; with several matches since local midnight, the latest wins.

You must be a member of the channel; otherwise Slack answers `not_in_channel`
and the post exits 9.
