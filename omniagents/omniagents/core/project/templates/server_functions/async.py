from omniagents import server_function

@server_function
async def {sanitized}(value: int = 1) -> dict:
    return {"ok": True, "value": value}
