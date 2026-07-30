"""Envelope <-> schema drift gate.

``omni_code/project.yml`` declares the coding summary envelope
(``sessions.summary_envelope``) — the content keys the framework
ensures on every ``omni sessions summarize`` result. The conventional
``session_summarizer`` agent's ``instructions.md`` tells the model to
emit a JSON object whose schema mirrors that envelope plus the identity
keys ``summarize_session`` stamps itself. The manifest comment admits
the two are a mirror; this test is the tripwire that keeps them equal.
"""

import json
from pathlib import Path

from omniagents.core.session.summarize import manifest_session_envelope

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "omni_code" / "project.yml"
INSTRUCTIONS = REPO / "omni_code" / "agents" / "session_summarizer" / "instructions.md"

# Identity keys summarize_session sets itself (out.setdefault(...) in
# omniagents.core.session.summarize) — present in the prompt's schema so
# the model echoes them, but never part of the manifest envelope.
IDENTITY_KEYS = {"session_id", "project", "agent", "created_at", "message_count"}


def _schema_block(text: str) -> dict:
    """The first balanced ``{...}`` JSON object in *text*, parsed."""
    start = text.index("{")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : i + 1])
    raise AssertionError("no balanced JSON object found in instructions.md")


def test_manifest_envelope_matches_instructions_schema():
    envelope = manifest_session_envelope(MANIFEST)
    assert envelope, "project.yml must declare sessions.summary_envelope"

    schema = _schema_block(INSTRUCTIONS.read_text(encoding="utf-8"))

    assert IDENTITY_KEYS <= set(schema), (
        "instructions.md schema lost identity keys: "
        f"{sorted(IDENTITY_KEYS - set(schema))}"
    )
    assert set(schema) - IDENTITY_KEYS == set(envelope), (
        "sessions.summary_envelope (project.yml) and the JSON schema in "
        "session_summarizer/instructions.md have drifted apart.\n"
        f"  schema-only:   {sorted(set(schema) - IDENTITY_KEYS - set(envelope))}\n"
        f"  envelope-only: {sorted(set(envelope) - set(schema))}"
    )


def test_envelope_defaults_match_schema_shapes():
    envelope = manifest_session_envelope(MANIFEST)
    schema = _schema_block(INSTRUCTIONS.read_text(encoding="utf-8"))
    for key, default in envelope.items():
        expected = list if isinstance(default, list) else str
        assert isinstance(schema[key], expected), (
            f"envelope key {key!r} defaults to {default!r} but the "
            f"instructions schema shows {schema[key]!r}"
        )
