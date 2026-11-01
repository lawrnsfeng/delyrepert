import sys

import structlog


def configure_console_logging() -> None:
    """Log human-readable lines to the current stderr (CLI commands)."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty()),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def get_logger() -> structlog.typing.FilteringBoundLogger:
    """Return the package logger."""
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger("daily_report")
    return logger
