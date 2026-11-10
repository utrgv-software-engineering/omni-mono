"""Build Workplace's UI and Python distributions on Linux, macOS, or Windows."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-install", action="store_true", help="Reuse the existing npm install"
    )
    parser.add_argument(
        "--assets-only", action="store_true", help="Only rebuild packaged UI assets"
    )
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    repo = package.parent
    npm = shutil.which("npm")
    if not npm or not (repo / "package-lock.json").is_file():
        parser.error("Build from the omni-mono checkout with Node/npm installed")
    if not args.skip_install:
        subprocess.run([npm, "ci"], cwd=repo, check=True)
    subprocess.run(
        [npm, "run", "build", "--workspace", "omniagents-workplace-web"],
        cwd=repo,
        check=True,
    )
    built = package / "web" / "dist"
    if not (built / "index.html").is_file():
        raise RuntimeError("Frontend build did not produce index.html")
    static = package / "omniagents_workplace" / "static"
    shutil.rmtree(static, ignore_errors=True)
    shutil.copytree(built, static)
    if not args.assets_only:
        # Remove only generated build output: setuptools otherwise retains
        # obsolete files when a module or hashed frontend chunk is removed.
        for directory in (package / "build", package / "dist"):
            shutil.rmtree(directory, ignore_errors=True)
        subprocess.run([sys.executable, "-m", "build"], cwd=package, check=True)


if __name__ == "__main__":
    main()
