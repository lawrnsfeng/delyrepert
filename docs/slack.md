# Slack setup for `daily-report post`

Slack will not give you a token? `daily-report standup` and `daily-report self` need none:
they print the report to paste by hand. See the README section "No Slack token? Copy and paste".

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

`slack.parent_match` picks the standup parent message. Every field you set must
match; set at least one.

| Field | Matches |
| --- | --- |
| `user_id` | The author's member ID (profile → *⋮* → *Copy member ID*). |
| `bot_id` | The posting bot's ID (`B…`), for a parent a bot or workflow sends. |
| `username` | The display name a bot or webhook posts under. |
| `text_contains` | Text the parent contains, e.g. `Daily standup`. |
| `text_regex` | A Python regex searched in the parent's text; checked when the config loads. |

Text rules read the message `text` plus the `text`, `fallback`, `pretext` and
`title` strings in its blocks and attachments, so a bot parent whose `text` is
empty still matches:

```toml
[slack]
channel = "C0123456789"
parent_match = { bot_id = "B0123456789", text_contains = "Daily standup" }
lookback_days = 1  # also look at yesterday (0-7; 0 = since local midnight)
```

With several matches in the window, the latest wins. Named patterns add other
threads; `channel` defaults to `slack.channel`, `lookback_days` to 0:

```toml
[slack.threads.weekly]
channel = "C0987654321"
parent_match = { username = "Weekly bot", text_regex = "^Weekly sync" }
lookback_days = 7
```

Names are lowercase letters, digits, `-` or `_`; `default` is the top-level
pattern. `daily-report thread list` prints them all, `thread show [--thread
NAME]` prints the parent each finds. `post`, `approve`, `preview` and `standup`
take `--thread NAME`.

`daily-report thread pin <permalink>` (or `/standup thread <permalink>`) pins a
thread for the next post; a link to a reply pins its thread. The precedence is
an explicit `--thread` name, then today's pin, then the default pattern;
`--target channel` ignores pins. A pin ends after the next real post or at
local midnight; `thread clear` removes it earlier.

Reading history uses the user token with `channels:history` (public) or
`groups:history` (private channels).

You must be a member of the channel; otherwise Slack answers `not_in_channel`
and the post exits 9.
