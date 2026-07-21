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
        "omni_code.agents." + path.name
        for path in (ROOT / "omni_code" / "agents").iterdir()
        if path.is_dir() and (path / "context.py").exists()
    }

    assert expected_agent_packages <= discovered
    assert "omni_code.agents.worker" in discovered
    assert "omni_code.tools" in discovered
    assert "omni_code.server_functions" in discovered

    # The packaged layout must not leak generic top-level package names —
    # that's the collision class the restructure eliminated.
    assert not any(
        pkg == leaked or pkg.startswith(leaked + ".")
        for pkg in discovered
        for leaked in ("tools", "capabilities", "server_functions", "omni_agents")
    )


def test_wheel_is_fully_self_contained(tmp_path):
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    wheel_path = next(tmp_path.glob("omni_code-*.whl"))

    with zipfile.ZipFile(wheel_path) as archive:
        names = set(archive.namelist())

    # Everything lives inside the omni_code package.
    assert "omni_code/project.yml" in names
    # The declared model catalog (paths.model_catalog) must ship — the
    # model store is built from it at product bootstrap.
    assert "omni_code/models.yml" in names
    assert "omni_code/capabilities/workers.yml" in names
    assert "omni_code/agents/worker/context.py" in names
    assert "omni_code/agents/omni/agent.yml" in names
    assert "omni_code/agents/omni/instructions.md" in names
    # Shared instruction fragments ({% include %} targets) must ship.
    assert "omni_code/agents/_shared/identity.md" in names
    assert "omni_code/overlays/platform.yml" in names
    assert "omni_code/sandbox_profile_default.yml" in names

    # No generic top-level packages escape into site-packages.
    top_level = {n.split("/", 1)[0] for n in names}
    for leaked in ("tools", "capabilities", "server_functions", "omni_agents"):
        assert leaked not in top_level, f"wheel leaks top-level {leaked}/"


def test_installed_wheel_project_loads_without_promotion_hacks(tmp_path):
    """The real convention test: unpack the wheel somewhere that is NOT the
    repo and load the project from inside the installed package — no path
    rewriting, no promotion, no sibling symlinks."""
    subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(tmp_path)],
        cwd=ROOT,
        check=True,
    )
    wheel_path = next(tmp_path.glob("omni_code-*.whl"))
    site = tmp_path / "site"
    with zipfile.ZipFile(wheel_path) as archive:
        archive.extractall(site)

    probe = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "import omni_code, pathlib\n"
        "pkg = pathlib.Path(omni_code.__file__).parent\n"
        "assert str(pkg).startswith(sys.argv[1]), pkg\n"
        "from omniagents.core.project.runtime import load_project\n"
        "proj = load_project(str(pkg / 'project.yml'))\n"
        "assert 'omni' in proj.agents_map, sorted(proj.agents_map)\n"
        "assert 'worker' in proj.agents_map\n"
        "assert 'exploration' in proj.capability_registry, sorted(proj.capability_registry)\n"
        "print('installed-wheel project load OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe, str(site)],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),  # neutral cwd: repo layout must not be reachable
    )
    assert result.returncode == 0, result.stderr
    assert "installed-wheel project load OK" in result.stdout
