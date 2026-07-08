"""
Structured logging setup — shared by the API process and standalone scripts.

Wraps stdlib logging so that both first-party `structlog.get_logger(__name__)`
calls and third-party stdlib loggers (uvicorn, sqlalchemy, langchain) render
through the same processor pipeline and end up in the same format.
"""

import logging
import sys

import structlog

from core.config import settings

_SHARED_PROCESSORS = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso"),
    structlog.processors.StackInfoRenderer(),
]


def configure_logging() -> None:
    """
    Configure structlog + stdlib logging. Call once at process startup
    (api/main.py at import time, scripts/reindex.py's main()) before any
    logger is used.

    DEBUG=true renders human-readable console output; otherwise renders
    single-line JSON, suitable for log aggregation in production.
    """
    structlog.configure(
        processors=_SHARED_PROCESSORS + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.dev.ConsoleRenderer() if settings.DEBUG else structlog.processors.JSONRenderer()
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
