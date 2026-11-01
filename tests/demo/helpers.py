"""Runners and builders for the demo tests."""

import datetime as dt
import json
import sys
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from typer.testing import CliRunner, Result

from daily_report.cli import app
from daily_report.compose.models import STYLE_NAMES

FIXTURES = Path(str(resources.files("daily_report.demo") / "fixtures"))
GOLDEN = Path(__file__).parent / "golden"
NOW = dt.datetime(2025, 11, 2, 10, 0, tzinfo=dt.UTC)
TOKEN = "xoxp-demo-token"  # noqa: S105 - a fake
LINK = "https://app.slack.com/block-kit-builder/#"
STUB = """
import json, sys, time
request = json.load(sys.stdin)
mode = sys.argv[1]
ids = [item["id"] for item in request["items"]]
answer = {"headline": "Cooked headline", "summaries": {i: f"Cooked {i}" for i in ids}}
if mode == "structured":
    answer = {"structured_output": answer}
elif mode == "missing":
    del answer["summaries"][ids[0]]
elif mode == "extra":
    answer["summaries"]["PAY-999"] = "x"
elif mode == "long":
    answer["summaries"][ids[0]] = "x" * 121
elif mode == "field":
    answer["status"] = "Done"
elif mode == "array":
    answer = []
elif mode == "invalid":
    sys.exit(print("not json"))
elif mode == "exit3":
    sys.exit(3)
elif mode == "sleep":
    time.sleep(5)
print(json.dumps(answer))
"""


def write_stub(root: Path) -> Path:
    """Write the cook stub script and return its path."""
    path = root / "cook_stub.py"
    path.write_text(STUB, encoding="utf-8")
    return path


@dataclass
class DemoEnv:
    """A config file, an out dir and a fake Slack URL for one test."""

    root: Path
    api_url: str = "http://127.0.0.1:1/api/"

    @property
    def config(self) -> Path:
        """Return the config path."""
        return self.root / "config.toml"

    @property
    def out(self) -> Path:
        """Return the `--out` dir."""
        return self.root / "out"

    def write_config(self, demo: str = "", extra: str = "") -> None:
        """Write a config with `[slack].api_url` at the fake, a `[demo]` table and `extra`."""
        text = 'timezone = "UTC"\n[jira]\nbase_url = "https://jira.example"\nemail = "a@b.c"\n'
        text += '[slack]\nchannel = "C0REAL"\nparent_match = { user_id = "U1" }\n'
        text += f'api_url = "{self.api_url}"\n'
        self.config.write_text(text + demo + extra, encoding="utf-8")

    def write_live_config(self, extra: str = "") -> None:
        """Write a config naming the demo workspace and channel."""
        self.write_config('[demo]\nslack_team_id = "T0DEMO"\nchannel = "C0DEMO"\n', extra)

    def write_cook_config(self, mode: str, timeout_s: int = 60) -> None:
        """Write a config whose `cook.command` runs the stub in `mode`."""
        argv = json.dumps([sys.executable, str(write_stub(self.root)), mode])
        self.write_config(extra=f"[cook]\ncommand = {argv}\ntimeout_s = {timeout_s}\n")

    def run(
        self,
        *args: str,
        token: str | None = None,
        stdin: str | None = None,
        dry_run: bool = False,
        config: bool = True,
    ) -> Result:
        """Run `daily-report [--dry-run] demo --out <out> <args>`."""
        argv = [*(["--dry-run"] if dry_run else []), "demo", "--out", str(self.out)]
        if config:
            argv += ["--config", str(self.config)]
        env = {"DAILY_REPORT_DEMO_SLACK_USER_TOKEN": token} if token else {}
        return CliRunner().invoke(app, [*argv, *args], input=stdin, env=env)


def out_names(out: Path) -> list[str]:
    """Return the file names under `out`."""
    return sorted(path.name for path in out.iterdir())


STYLE_FILES = sorted(f"{name}.json" for name in STYLE_NAMES)
