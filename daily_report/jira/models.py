"""Jira Cloud REST v3 response shapes, limited to the fields daily-report reads."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, RootModel, TypeAdapter
from pydantic.alias_generators import to_camel


class JiraModel(BaseModel):
    """Base for Jira payloads: camelCase on the wire, unknown keys ignored."""

    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)


class JiraUser(JiraModel):
    """A user or app account (`/myself`, comment and changelog authors)."""

    account_id: str
    display_name: str
    account_type: str = "atlassian"


class JiraField(JiraModel):
    """One entry of `GET /rest/api/3/field`."""

    id: str
    name: str


class JiraFieldList(RootModel[list[JiraField]]):
    """`GET /rest/api/3/field` response."""


class StatusCategory(JiraModel):
    """A status category; `key` is `new`, `indeterminate` or `done`."""

    key: str


class JiraStatus(JiraModel):
    """An issue status."""

    name: str
    status_category: StatusCategory


class JiraPriority(JiraModel):
    """An issue priority."""

    name: str


class FlagOption(JiraModel):
    """One selected option of a multi-select field such as Flagged."""

    value: str


_FLAGS = TypeAdapter(list[FlagOption | str] | None)


class IssueFields(JiraModel):
    """The issue fields daily-report searches for, plus any custom field (kept as extra)."""

    model_config = ConfigDict(extra="allow")

    summary: str
    status: JiraStatus
    priority: JiraPriority | None = None
    duedate: date | None = None
    updated: datetime

    def flags(self, field_id: str) -> list[str]:
        """Return the selected values of custom field `field_id`."""
        options = _FLAGS.validate_python((self.model_extra or {}).get(field_id)) or []
        return [option if isinstance(option, str) else option.value for option in options]


class JiraIssue(JiraModel):
    """One issue of a `search/jql` page."""

    id: str
    key: str
    fields: IssueFields


class SearchPage(JiraModel):
    """`POST /rest/api/3/search/jql` response."""

    issues: list[JiraIssue]
    next_page_token: str | None = None


class ChangeItem(JiraModel):
    """One changed field inside a changelog history entry."""

    field: str
    from_string: str | None = None
    to_string: str | None = None


class ChangeHistory(JiraModel):
    """One changelog history entry."""

    id: str
    author: JiraUser | None = None
    created: datetime
    items: list[ChangeItem]


class IssueChangeLog(JiraModel):
    """The changelog of one issue in a bulkfetch page."""

    issue_id: str
    change_histories: list[ChangeHistory]


class ChangeLogPage(JiraModel):
    """`POST /rest/api/3/changelog/bulkfetch` response."""

    issue_change_logs: list[IssueChangeLog]
    next_page_token: str | None = None


class AdfNode(JiraModel):
    """A node of an Atlassian Document Format (Jira rich-text) body."""

    type: str
    text: str | None = None
    content: list["AdfNode"] = []

    @property
    def plain_text(self) -> str:
        """Concatenated text of this node and its children, blocks separated by spaces."""
        return self.text or " ".join(child.plain_text for child in self.content)


class JiraComment(JiraModel):
    """One issue comment."""

    id: str
    author: JiraUser
    body: AdfNode
    created: datetime


class CommentPage(JiraModel):
    """`GET /rest/api/3/issue/{key}/comment` response."""

    comments: list[JiraComment]
