"""Architecture guardrails for execution-environment state ownership."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[4] / "omniagents"


def test_production_code_cannot_restore_process_global_serve_state() -> None:
    """Execution placement belongs to RequestContext and EnvironmentLease."""
    deleted_module = PACKAGE_ROOT / "core" / "runtime" / "serve_state.py"
    assert not deleted_module.exists()

    forbidden_import = "omniagents.core.runtime.serve_state"
    offenders = [
        path.relative_to(PACKAGE_ROOT)
        for path in PACKAGE_ROOT.rglob("*.py")
        if forbidden_import in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
