"""Command-line entry point for the Stackfuel extraction pipeline."""

from pipeline_functions.helpers.logging_utils import get_logger
from pipeline_functions.workflows.runner import main

logger = get_logger(__name__)


if __name__ == "__main__":
    logger.info("Starting Stackfuel extraction pipeline")
    main()
