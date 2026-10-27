from agents.tool import function_tool


@function_tool
def greet_tool(name: str) -> str:
    return f"Hello {name}"


greet_tool._is_omniagents_core_tool = True
