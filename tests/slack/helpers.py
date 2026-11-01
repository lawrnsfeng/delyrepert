"""Builders and a CLI runner for the `post` tests."""

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.compose.models import Draft
from daily_report.notes.models import Note
from tests.compose.helpers import r1_draft

PARENT_TS = "1762074000.000100"
OLD_TS = "1761814800.000100"  # 2025-10-30
NOW = dt.datetime(2025, 11, 2, 10, 0, tzinfo=dt.UTC)
RUN_ID = r1_draft().run_id
TOKEN = "xoxp-test-token"  # noqa: S105 - a fake


def notes() -> list[Note]:
    """Return 2 pending notes."""
    return [
        Note(at=dt.datetime(2025, 11, 1, 18, tzinfo=dt.UTC), text="Out 15:00", group="today"),
        Note(
            at=dt.datetime(2025, 11, 1, 19, tzinfo=dt.UTC),
            text="Needs review",
            group="attention",
        ),
    ]


def write_draft(
    state: Path,
    pending: list[Note],
    *,
    posted: dict[str, str] | None = None,
) -> Draft:
    """Save R1's draft with `pending` notes (and `posted`) under `state`."""
    draft = r1_draft(notes=pending)
    if posted:
        draft = Draft.model_validate({**draft.model_dump(mode="json"), "posted": posted})
    path = state / "drafts" / f"{draft.run_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return draft


@dataclass
class SlackEnv:
    """A config file, a state dir and the fake Slack URL for one test."""

    root: Path
    state: Path
    api_url: str

    @property
    def config(self) -> Path:
        """Return the config path."""
        return self.root / "config.toml"

    @property
    def inbox(self) -> Path:
        """Return the notes inbox path."""
        return self.state / "notes" / "pending.jsonl"

    @property
    def draft_file(self) -> Path:
        """Return R1's draft path."""
        return self.state / "drafts" / f"{RUN_ID}.json"

    def write_config(
        self,
        *,
        match: str = 'user_id = "U0BOT"',
        extra: str = "",
        slack: bool = True,
        animated: bool = True,
    ) -> None:
        """Write a config pointing `slack.api_url` at the fake."""
        text = 'timezone = "UTC"\n\n[jira]\nbase_url = "https://jira.example"\n'
        text += 'email = "owner@example.com"\n'
        if animated:
            text += '\n[emoji]\nblocked_animated = "alert-anim"\n'
        if slack:
            text += f'\n[slack]\nchannel = "C0STANDUP"\napi_url = "{self.api_url}"\n'
            text += f"parent_match = {{ {match} }}\n"
        self.config.write_text(text + extra, encoding="utf-8")

    def post(
        self,
        *args: str,
        token: str | None = TOKEN,
        run_id: str = RUN_ID,
        stdin: str | None = None,
        dry_run: bool = False,
    ) -> Result:
        """Run `daily-report [--dry-run] post <run_id> <args>` against this env."""
        argv = [
            *(["--dry-run"] if dry_run else []),
            "post",
            run_id,
            "--config",
            str(self.config),
            "--state-dir",
            str(self.state),
            *args,
        ]
        env = {"SLACK_USER_TOKEN": token} if token else {}
        return CliRunner().invoke(app, argv, input=stdin, env=env)
