"""Create the format for logging."""

import logging
import sys

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-20s %(message)s",
)


def get_logger(logger_name: str) -> logging.Logger:
    """Return a logger using the pipeline-wide logging configuration."""
    return logging.getLogger(logger_name)
