"""A run's record of the skills its agent was offered and loaded."""

import time
from pathlib import Path

from omniagents.core.skills import default_skill_roots
from omniagents.core.skills.placement import RunSkill
from omniagents.core.skills.usage import record_loaded, record_offered, since


def test_a_turn_takes_what_was_offered_and_loaded_during_it():
    before = time.time()
    record_offered("s-usage", [RunSkill("old", "", "/x/old", "SKILL.md")])
    record_loaded("s-usage", "old", "/x/old")
    time.sleep(0.01)
    started = time.time()
    record_offered(
        "s-usage",
        [
            RunSkill(
                "triage",
                "",
                "skills/abc/triage",
                "SKILL.md",
                host_dir=Path("/f/skills/triage"),
            ),
            RunSkill("broken", "", "/x/broken", "SKILL.md", error="no name"),
        ],
    )
    record_loaded("s-usage", "triage", "/f/skills/triage")
    assert since("s-usage", started) == {
        "offered": [{"name": "triage", "path": "/f/skills/triage"}],
        "loaded": [{"name": "triage", "path": "/f/skills/triage"}],
    }
    assert len(since("s-usage", before)["loaded"]) == 2
    assert since("unknown", before) == {"offered": [], "loaded": []}


def test_a_factory_leaves_out_the_global_folder(tmp_path):
    with_global = default_skill_roots(str(tmp_path))
    without = default_skill_roots(str(tmp_path), include_global=False)
    assert without == with_global[:-1]
    assert with_global[-1].name == "skills"
