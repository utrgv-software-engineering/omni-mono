import os
from typing import Any

from dotenv import load_dotenv
from omniagents import Runner
from openai import AsyncAzureOpenAI, AsyncOpenAI
from agents import (
    set_default_openai_client,
    set_default_openai_api,
)
import agentio
from tools import *
from oagents.coding_agent import create_agent

# Load environment variables
load_dotenv()

# Configure OpenAI provider (Azure vs. OpenAI) based on environment
provider = (os.getenv("OPENAI_PROVIDER") or "openai").strip().lower()
if provider == "azure":
    client = AsyncAzureOpenAI(
        api_key=os.getenv("AZURE_OPENAI_API_KEY"),
        api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
        azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
        organization=os.getenv("OPENAI_ORG"),
        project=os.getenv("OPENAI_PROJECT"),
    )
    set_default_openai_client(client, use_for_tracing=False)
elif provider == "openai":
    client = AsyncOpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_BASE_URL"),
        organization=os.getenv("OPENAI_ORG"),
        project=os.getenv("OPENAI_PROJECT"),
    )
    set_default_openai_client(client, use_for_tracing=True)
else:
    raise ValueError(
        f"Unsupported OPENAI_PROVIDER: {provider!r}. Use 'azure' or 'openai'."
    )


def cli_main():
    agentio.init(
        project="omni_code",
        name="omni_code",
        resume="never",
    )

    agent = create_agent()

    runner = Runner.from_agent(
        agent,
        welcome_text="Welcome to Omni Code! I'm here to help you with your coding needs.",
        max_turns=100,
        context_window=400000,
    )

    def _register_server_functions(service: Any) -> None:
        async def _set_note(_service, session, args, is_good: bool):
            text = (args or {}).get("text", "")
            setattr(session, "_omnicode_note", {"text": text, "is_good": bool(is_good)})
            label = "good" if is_good else "bad"
            return {"ok": True, "message": f"Saved {label} note: {text}"}

        async def good(_service, session, args):
            result = await _set_note(_service, session, args, True)
            try:
                group_id = f"session_{session.id}"
                text = (args or {}).get("text")
                agentio.set_conversation_analysis(
                    group_id,
                    judgment="acceptable",
                    notes=text,
                    marked=True,
                )
            except Exception:
                pass
            return result

        async def bad(_service, session, args):
            result = await _set_note(_service, session, args, False)
            try:
                group_id = f"session_{session.id}"
                text = (args or {}).get("text")
                agentio.set_conversation_analysis(
                    group_id,
                    judgment="unacceptable",
                    notes=text,
                    marked=True,
                )
            except Exception:
                pass
            return result

        async def get_note(_service, session, _args):
            try:
                group_id = f"session_{session.id}"
                analysis = agentio.get_conversation_analysis(group_id)
                notes = analysis.get("notes") or ""
                judgment = analysis.get("judgment")
                if not notes or judgment not in ("acceptable", "unacceptable"):
                    return {"note": None, "message": "No note set."}
                is_good = judgment == "acceptable"
                label = "Good" if is_good else "Bad"
                return {"note": {"text": notes, "is_good": is_good}, "message": f"{label}: {notes}"}
            except Exception:
                return {"note": None, "message": "No note set."}

        try:
            service.register_server_function(
                "good",
                good,
                description="Mark conversation acceptable and submit feedback to developers (optional note included)",
                params_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
            service.register_server_function(
                "bad",
                bad,
                description="Flag conversation as unacceptable and submit feedback to developers (optional note included)",
                params_schema={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            )
            service.register_server_function(
                "note",
                get_note,
                description="Get conversation analysis summary (Good/Bad and notes) saved for developer review",
            )
            service.register_server_function(
                "notes",
                get_note,
                description="Alias of 'note' (analysis summary for developer review)",
            )
        except Exception:
            pass

    runner.spec.register_server_functions = _register_server_functions

    runner.main(default_mode="tui")


if __name__ == "__main__":
    cli_main()
