from omniagents import function_tool
from datetime import datetime


@function_tool
def get_current_time() -> str:
    """
    Get the current time in HH:MM:SS format.
    
    Returns:
        The current time as a string
    """
    return datetime.now().strftime("%H:%M:%S")