"""Simple time tool for testing."""

from datetime import datetime


def get_time() -> str:
    """Get the current time.

    Returns:
        str: Current time in HH:MM:SS format
    """
    return datetime.now().strftime("%H:%M:%S")
