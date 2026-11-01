import asyncio
import json

from pydantic import ValidationError

from daily_report.compose.models import Draft
from daily_report.cook.exceptions import (
    CookDroppedItemError,
    CookExitError,
    CookInvalidJsonError,
    CookInvalidOutputError,
    CookRejectedError,
    CookStartError,
    CookTimeoutError,
    CookUnknownItemError,
)
from daily_report.cook.models import CookOutput


class CookRunner:
    """Runs `cook.command` with the draft JSON on stdin, within a timeout."""

    def __init__(self, argv: list[str], timeout_s: int) -> None:
        """Run `argv`; kill it after `timeout_s` seconds."""
        self._argv = argv
        self._timeout_s = timeout_s

    async def run(self, stdin: str) -> str:
        """Return the command's stdout; raise `CookRejectedError` on any failure."""
        try:
            process = await asyncio.create_subprocess_exec(
                *self._argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CookStartError(str(exc)) from exc
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(stdin.encode()),
                self._timeout_s,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise CookTimeoutError(self._timeout_s) from exc
        if process.returncode:
            raise CookExitError(process.returncode)
        return stdout.decode(errors="replace")


def parse_output(raw: str, ids: set[str]) -> CookOutput:
    """Validate the cook output (bare or under `structured_output`) against the visible `ids`."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CookInvalidJsonError from exc
    if isinstance(data, dict) and "structured_output" in data:
        data = data["structured_output"]
    try:
        output = CookOutput.model_validate(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "output"
        raise CookInvalidOutputError(where, first["msg"]) from exc
    for key in output.summaries:
        if key not in ids:
            raise CookUnknownItemError(key)
    missing = sorted(ids - output.summaries.keys())
    if missing:
        raise CookDroppedItemError(missing[0])
    return output


class CookService:
    """Lets an AI reword the draft; anything but wording is rejected and the script text stays."""

    def __init__(self, runner: CookRunner) -> None:
        """Polish drafts with `runner`."""
        self._runner = runner

    async def polish(self, draft: Draft) -> Draft:
        """Return `draft` reworded (`cooked`), or unchanged with `cook_error` saying why."""
        visible = draft.visible
        stdin = json.dumps(
            {
                "headline": draft.headline,
                "items": [
                    {"id": i.id, "kind": i.kind, "title": i.title, "summary": i.summary}
                    for i in visible
                ],
            },
            ensure_ascii=False,
        )
        try:
            output = parse_output(await self._runner.run(stdin), {i.id for i in visible})
        except CookRejectedError as exc:
            return draft.model_copy(update={"cook_error": exc.message})
        items = [
            item.model_copy(update={"summary": output.summaries[item.id]})
            if item.id in output.summaries
            else item
            for item in draft.items
        ]
        return draft.model_copy(
            update={"headline": output.headline, "items": items, "cooked": True},
        )
