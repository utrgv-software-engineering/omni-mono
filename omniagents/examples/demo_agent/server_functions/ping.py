from omniagents import server_function

@server_function
def ping(value: int = 1) -> dict:
    return {'ok': True, 'value': value}
