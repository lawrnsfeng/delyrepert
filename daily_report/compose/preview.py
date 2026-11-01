"""The terminal preview and its prompt loop: `[p]ost [e]dit [s]tyle [t]arget [c]ancel`."""

import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import tomlkit
import typer

from daily_report.compose.edit import apply_edit, to_yaml, with_error
from daily_report.compose.exceptions import InvalidEditError
from daily_report.compose.models import STYLE_NAMES, Draft, StyleName, Target
from daily_report.compose.render import Layout, Renderer
from daily_report.compose.services import DraftStore
from daily_report.config.models import Config
from daily_report.effects.models import Intent
from daily_report.effects.services import DryRunEffects, Effects
from daily_report.slack.commands import _post
from daily_report.slack.exceptions import NoParentError
from daily_report.slack.services import PostService

KEYS = ("p", "e", "s", "t", "c")
PROMPT = "[p]ost [e]dit [s]tyle [t]arget [c]ancel"
TARGETS: tuple[Target, ...] = ("thread", "thread_broadcast", "channel")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def is_interactive() -> bool:
    """Return whether the owner can answer prompts (tests replace it)."""
    return sys.stdin.isatty()


def preview_text(draft: Draft, display_name: str | None) -> str:
    """Return the draft as plain lines: header, counts, then the BLUF groups."""
    layout = Layout(draft, display_name, None)
    lines = [layout.header, *([layout.counts] if layout.counts else [])]
    for part in layout.bluf:
        lines += ["", part.title]
        for entry in part.entries:
            ref = f"{entry.label} " if entry.label else ""
            lines.append(f"  {entry.icon.emoji} {entry.icon.word} · {ref}{entry.text}")
    return "\n".join(lines)


def _rewrite_style(config_path: Path, style: StyleName) -> None:
    document = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    document.setdefault("render", tomlkit.table())["style"] = style
    config_path.write_text(tomlkit.dumps(document), encoding="utf-8")


async def save_style(config_path: Path, style: StyleName, effects: Effects) -> None:
    """Write `render.style` into the config file with tomlkit, which keeps comments."""

    async def write() -> None:
        _rewrite_style(config_path, style)

    await effects.perform(Intent("write", f'render.style = "{style}" to {config_path}'), write)


class PreviewLoop:
    """Shows the draft in one or all styles and runs the prompt loop."""

    def __init__(  # noqa: PLR0913 - the collaborators a session needs
        self,
        draft: Draft,
        *,
        settings: Config,
        state_dir: Path,
        config_path: Path,
        effects: Effects,
        service: Callable[[], PostService],
        style: StyleName | None,
        gallery: bool,
    ) -> None:
        """Preview `draft`; `service` builds the poster only when the owner chooses `p`."""
        self.draft = draft
        self._settings = settings
        self._state_dir = state_dir
        self._config_path = config_path
        self._effects = effects
        self._store = DraftStore(state_dir, effects)
        self._renderer = Renderer(
            display_name=settings.display_name,
            blocked_animated=settings.emoji.blocked_animated,
        )
        self._service = service
        self._style = style
        self._gallery = gallery
        self._target: Target = settings.post.target
        self._dry_run = isinstance(effects, DryRunEffects)

    def show(self) -> None:
        """Print the plain preview and one Builder link per style (labelled in the gallery)."""
        draft = self.draft
        typer.echo(f"draft {draft.run_id}")
        typer.echo(preview_text(draft, self._settings.display_name))
        if draft.cook_error:
            typer.echo(f"cook rejected: {draft.cook_error}")
        styles = [self._style] if self._style and not self._gallery else list(STYLE_NAMES)
        for number, name in enumerate(styles, 1):
            if len(styles) > 1:
                typer.echo(f"Style {number}/{len(styles)} — {name}")
            payload = self._renderer.render(draft, name, animated_available=False)
            typer.echo(payload.builder_url)

    async def run(self) -> None:
        """Show the preview, then prompt (or, without a terminal, print how to post)."""
        self.show()
        if not is_interactive():
            style = self._style or STYLE_NAMES[0]
            typer.echo(f"post with: daily-report post {self.draft.run_id} --style {style} --yes")
            return
        while True:
            key = typer.prompt(PROMPT).strip().lower()
            if key not in KEYS:
                typer.echo(f"choose one of {', '.join(KEYS)}")
            elif key == "p":
                await self._post()
                return
            elif key == "c":
                typer.echo("cancelled")
                return
            elif key == "e":
                await self._edit()
                self.show()
            elif key == "s":
                self._pick_style()
                self.show()
            else:
                self._target = TARGETS[(TARGETS.index(self._target) + 1) % len(TARGETS)]
                typer.echo(f"target: {self._target}")

    def _pick_style(self) -> StyleName:
        for number, name in enumerate(STYLE_NAMES, 1):
            typer.echo(f"{number}) {name}")
        number = 0
        while not 1 <= number <= len(STYLE_NAMES):
            number = typer.prompt(f"Style 1-{len(STYLE_NAMES)}", type=int)
        self._style = STYLE_NAMES[number - 1]
        self._gallery = False
        return self._style

    async def _post(self) -> None:
        style = self._style or self._pick_style()
        service = self._service()
        try:
            permalink = await self._send(service, style)
        except NoParentError as exc:
            typer.echo(str(exc))
            if not typer.confirm(f"Post top-level in #{exc.channel} instead?", default=False):
                return
            self._target = "channel"
            permalink = await self._send(service, style)
        if permalink:
            typer.echo(permalink)
        if self._settings.render.style is None and typer.confirm(
            f"Save {style} as the default style?",
            default=False,
        ):
            await save_style(self._config_path, style, self._effects)

    async def _send(self, service: PostService, style: StyleName) -> str:
        return await _post(
            service,
            self.draft.run_id,
            style=style,
            target=self._target,
            yes=True,
            preview=self._dry_run,
            draft=self.draft,
        )

    async def _edit(self) -> None:
        """Open the edit YAML until it validates; save the result."""
        name = f"{self.draft.run_id}.edit.yaml"
        # Dry-run edits a scratch file in a temp dir so `<state_dir>` stays untouched.
        folder = Path(tempfile.mkdtemp(prefix="daily-report-")) if self._dry_run else None
        path = (folder or self._state_dir / "drafts") / name
        text = to_yaml(self.draft)
        while True:
            await self._write(path, text)
            self._open_editor(path)
            text = path.read_text(encoding="utf-8")
            try:
                edited = apply_edit(self.draft, text)
            except InvalidEditError as exc:
                text = with_error(text, exc.message.removeprefix("invalid edit: "))
                continue
            break
        path.unlink()
        if folder:
            folder.rmdir()
        self.draft = edited
        await self._store.save(edited)

    async def _write(self, path: Path, text: str) -> None:
        async def write() -> None:
            _write_text(path, text)

        if self._dry_run:
            await write()
        else:
            await self._effects.perform(Intent("write", str(path)), write)

    @staticmethod
    def _open_editor(path: Path) -> None:
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if editor:
            subprocess.run([*shlex.split(editor), str(path)], check=False)  # noqa: S603
        else:
            typer.echo(f"edit {path} then press Enter")
            typer.prompt("", default="", show_default=False)
