from daily_report.compose.models import StyleName, Target

VERBS = ("run", "preview", "status", "pause", "resume", "note", "help")
ACTION_POST = "standup_post"
ACTION_EDIT_NOTES = "standup_edit_notes"
ACTION_SKIP = "standup_skip"
ACTION_STYLE = "standup_style"
ACTION_TARGET = "standup_target"
ACTION_IDS = (ACTION_POST, ACTION_EDIT_NOTES, ACTION_SKIP, ACTION_STYLE, ACTION_TARGET)
NOTES_MODAL = "standup_notes_modal"
NOTE_BLOCK = "note"
NOTE_INPUT = "text"
DEFAULT_STYLE: StyleName = "bluf"
TARGETS: tuple[Target, ...] = ("thread", "thread_broadcast", "channel")
HELP = "usage: /standup " + "|".join(VERBS)
