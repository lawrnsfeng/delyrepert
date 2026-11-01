import asyncio

import typer
from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler
from slack_sdk.web.async_client import AsyncWebClient

from daily_report.collect.commands import ConfigOption, StateDirOption
from daily_report.config.exceptions import ConfigError, SlackUnsetError
from daily_report.effects.commands import DryRunOption
from daily_report.effects.models import Intent
from daily_report.effects.services import DryRunEffects
from daily_report.logger import configure_json_logging, get_logger
from daily_report.retry import RetryPolicy
from daily_report.service.commands import SecretsFileOption, _Env, _fail
from daily_report.slack.exceptions import SlackError
from daily_report.slack.services import SlackClient
from daily_report.slack_app.app import build_app
from daily_report.slack_app.dm import DmService
from daily_report.slack_app.handlers import StandupHandlers
from daily_report.slack_app.models import ACTION_IDS, VERBS
from daily_report.slack_app.timer import timer_next


def serve(
    ctx: typer.Context,
    *,
    config: ConfigOption = None,
    state_dir: StateDirOption = None,
    secrets_file: SecretsFileOption = None,
    dry_run: DryRunOption = False,
) -> None:
    """Listen for `/standup` and the approval DM's buttons over Socket Mode (no public URL)."""
    env = _Env(ctx, config, state_dir, dry_run=dry_run)
    env.with_secrets(secrets_file)
    owner = env.owner()
    try:
        slack = env.settings.slack
        if slack is None:
            raise SlackUnsetError
        bot_token = env.resolver.slack_bot_token()
        app_token = env.resolver.slack_app_token()
        dms = DmService.from_config(
            env.settings,
            bot_token=bot_token,
            owner=owner,
            state_dir=env.root,
            effects=env.effects,
        )
    except ConfigError as exc:
        _fail(exc)
    web = AsyncWebClient(token=bot_token, base_url=slack.api_url)
    configure_json_logging()
    try:
        asyncio.run(_listen(env, dms, web, app_token, owner))
    except SlackError as exc:
        _fail(exc)


async def _listen(
    env: _Env,
    dms: DmService,
    web: AsyncWebClient,
    app_token: str,
    owner: str,
) -> None:
    identity = await SlackClient(web, RetryPolicy()).auth_test()
    handlers = StandupHandlers(env, dms, timer_next)
    app = build_app(handlers, owner=owner, web=web)

    async def connect() -> None:
        get_logger().info("serving", owner=owner)
        await AsyncSocketModeHandler(app, app_token, web_client=web).start_async()  # type: ignore[no-untyped-call]

    if isinstance(env.effects, DryRunEffects):  # a preflight: show what would be served
        typer.echo("verbs: " + " ".join(VERBS))
        typer.echo("actions: " + " ".join(ACTION_IDS))
    await env.effects.perform(Intent("open", f"Socket Mode as {identity['user_id']}"), connect)
