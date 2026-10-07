"""Credentials for the local test API; environment variables override defaults."""

import os


def get_api_credentials() -> tuple[str, str]:
    return (
        os.environ.get("API_USERNAME", "stackfuel"),
        os.environ.get("API_PASSWORD", "learn-data-2026"),
    )
