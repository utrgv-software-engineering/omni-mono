from omniagents import function_tool

@function_tool
def echo(text: str) -> str:
    return text

@function_tool
def word_count(text: str) -> int:
    return len(text.split()) if text else 0
