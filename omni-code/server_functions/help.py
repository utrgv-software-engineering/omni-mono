from omniagents import server_function


@server_function(description="Show available commands", strict=True, name_override="help")
async def help_function() -> dict:
    text = (
        "**Commands:**\n"
        " `/good` - Mark conversation acceptable and save an optional note for developers.\n"
        " `/bad` - Flag conversation unacceptable and save an optional note for developers.\n"
        " `/note` - Show saved analysis summary (Good/Bad and notes).\n"
        " `/notes` - Alias of `/note`.\n"
        " `/exit` - End the current session and exit the app."
    )
    return {"message": text}
