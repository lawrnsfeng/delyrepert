.PHONY: sync
sync:
	@uv sync --all-groups -U

.PHONY: lint
lint:
	@uv run ruff check --fix

.PHONY: tc
tc:
	@uv run mypy .

.PHONY: ty
ty:
	@uv run ty check

.PHONY: pre
pre:
	@uv run pre-commit run --all-files

.PHONY: test
test:
	@uv run python -m pytest --cov=daily_report --cov-branch --cov-fail-under=100 tests

.PHONY: testv
testv:
	@uv run python -m pytest -s tests

.PHONY: check
check: lint tc ty test

.PHONY: next-version
next-version:
	@uvx --from python-semantic-release semantic-release --noop version --print
