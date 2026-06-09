from pathlib import Path
import subprocess
import sys
import tomllib
import zipfile

from setuptools import find_namespace_packages


ROOT = Path(__file__).resolve().parents[1]


def test_all_agent_support_packages_are_discovered():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    package_find = pyproject["tool"]["setuptools"]["packages"]["find"]

    discovered = set(
        find_namespace_packages(
            where=str(ROOT),
            include=package_find["include"],
            exclude=package_find["exclude"],
        )
    )

    expected_agent_packages = {
        "omni_agents." + path.name
        for path in (ROOT / "omni_agents").iterdir()
        if path.is_dir() and (path / "context.py").exists()
    }

    assert expected_agent_packages <= discovered
    assert "omni_agents.explorer" in discovered
    assert "omni_agents.worker" in discovered


def test_wheel_contains_packaged_project_and_subagent_contexts(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    wheel_path = next(tmp_path.glob("omni_code-*.whl"))

    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())

    assert "omni_code/project.yml" in names
    assert "capabilities/exploration.yml" in names
    assert "omni_agents/explorer/context.py" in names
    assert "omni_agents/worker/context.py" in names
