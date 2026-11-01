from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from daily_report.collect.models import SUMMARY_MAX

type Line = Annotated[str, Field(max_length=SUMMARY_MAX)]


class CookOutput(BaseModel):
    """What `cook.command` prints: wording only, nothing else (decision D4)."""

    model_config = ConfigDict(extra="forbid")

    headline: Annotated[str, Field(min_length=1, max_length=SUMMARY_MAX)]
    summaries: dict[str, Line]
