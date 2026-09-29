import structlog


def configure_logging() -> None:
    """Structured JSON logs: every agent step and tool call becomes one log line."""
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )
