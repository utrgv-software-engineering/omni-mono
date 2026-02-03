from omniagents import server_function


@server_function(description="Show available commands", strict=True, name_override="help")
async def help_function() -> dict:
    text = (
        "**Commands:**\n"
        "\n"
        "*Model:*\n"
        " `/models` - List available models and current settings.\n"
        " `/model <name>` - Switch to a different model for this session.\n"
        " `/voice-models` - List available voice models.\n"
        " `/voice-model <name>` - Set the voice model (reconnect voice to apply).\n"
        " `/reasoning <low|medium|high>` - Set reasoning effort level.\n"
        "\n"
        "*Feedback:*\n"
        " `/good` - Mark conversation acceptable with optional note.\n"
        " `/bad` - Flag conversation unacceptable with optional note.\n"
        " `/note` - Show saved analysis summary.\n"
        "\n"
        "*Session:*\n"
        " `/compact` - Compact conversation context.\n"
        " `/exit` - End the current session and exit.\n"
        "\n"
        "*CLI:*\n"
        " `omni model setup` - Configure models.\n"
        " `omni model list` - List configured models.\n"
        " `omni model add` - Add a new model."
    )
    return {"message": text}
