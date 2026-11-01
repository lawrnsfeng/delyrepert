"""The Bolt app: owner guard, `/standup` router, DM actions and the notes modal."""

from typing import Any

from slack_bolt.async_app import AsyncAck, AsyncApp, AsyncRespond
from slack_bolt.response import BoltResponse
from slack_sdk.web.async_client import AsyncWebClient

from daily_report.compose.models import STYLE_NAMES, StyleName, Target
from daily_report.slack_app.handlers import StandupHandlers
from daily_report.slack_app.models import (
    ACTION_EDIT_NOTES,
    ACTION_POST,
    ACTION_SKIP,
    ACTION_STYLE,
    ACTION_TARGET,
    NOTE_BLOCK,
    NOTE_INPUT,
    NOTES_MODAL,
    TARGETS,
)

type Body = dict[str, Any]

NOT_AUTHORIZED = "not authorized"


def _selected(body: Body, action_id: str) -> str | None:
    for block in body.get("state", {}).get("values", {}).values():
        option = block.get(action_id, {}).get("selected_option")
        if option:
            return str(option["value"])
    return None


def _style(body: Body) -> StyleName | None:
    chosen = _selected(body, ACTION_STYLE)
    return next((style for style in STYLE_NAMES if style == chosen), None)


def _target(body: Body) -> Target | None:
    chosen = _selected(body, ACTION_TARGET)
    return next((target for target in TARGETS if target == chosen), None)


async def _reply(respond: AsyncRespond, text: str | None) -> None:
    if text:
        await respond(text=text, response_type="ephemeral", replace_original=False)


def _guard_owner(app: AsyncApp, owner: str) -> None:
    @app.middleware
    async def owner_only(
        body: Body,
        ack: AsyncAck,
        respond: AsyncRespond,
        next_: Any,  # noqa: ANN401 - Bolt's continuation
    ) -> BoltResponse | None:
        user = body.get("user_id") or body.get("user", {}).get("id")
        if user == owner:
            return await next_()
        answered = await ack()
        if body.get("response_url"):  # a modal has none, so its ack alone tells the sender no
            await _reply(respond, NOT_AUTHORIZED)
        return answered


def _register_command(app: AsyncApp, handlers: StandupHandlers) -> None:
    @app.command("/standup")
    async def standup(ack: AsyncAck, respond: AsyncRespond, command: Body) -> None:
        await ack()
        await _reply(respond, await handlers.guard(handlers.command(command.get("text", ""))))


def _register_actions(app: AsyncApp, handlers: StandupHandlers) -> None:
    @app.action(ACTION_POST)
    async def post(ack: AsyncAck, respond: AsyncRespond, body: Body) -> None:
        await ack()
        work = handlers.post(body["actions"][0]["value"], _style(body), _target(body))
        await _reply(respond, await handlers.guard(work))

    @app.action(ACTION_EDIT_NOTES)
    async def edit_notes(ack: AsyncAck, respond: AsyncRespond, body: Body) -> None:
        await ack()
        work = handlers.edit_notes(body["actions"][0]["value"], body["trigger_id"])
        await _reply(respond, await handlers.guard(work))

    @app.action(ACTION_SKIP)
    async def skip(ack: AsyncAck, respond: AsyncRespond, body: Body) -> None:
        await ack()
        await _reply(respond, await handlers.guard(handlers.skip(body["actions"][0]["value"])))

    @app.action(ACTION_STYLE)
    @app.action(ACTION_TARGET)
    async def selected(ack: AsyncAck) -> None:
        await ack()  # the choice rides along in `state` when Post is clicked


def _register_modal(app: AsyncApp, handlers: StandupHandlers) -> None:
    @app.view(NOTES_MODAL)
    async def notes(ack: AsyncAck, view: Body) -> None:
        text = view["state"]["values"][NOTE_BLOCK][NOTE_INPUT]["value"] or ""
        error = await handlers.guard(handlers.submit_note(view["private_metadata"], text))
        if error:
            await ack(response_action="errors", errors={NOTE_BLOCK: error})
        else:
            await ack()


def build_app(handlers: StandupHandlers, *, owner: str, web: AsyncWebClient) -> AsyncApp:
    """Return the Bolt app serving `owner`; Socket Mode needs no request signing."""
    app = AsyncApp(client=web, request_verification_enabled=False)
    _guard_owner(app, owner)
    _register_command(app, handlers)
    _register_actions(app, handlers)
    _register_modal(app, handlers)
    return app
