"""Smoke-check an installed Workplace distribution, without a source checkout."""

import json
import subprocess
import sys
import tempfile
from importlib import resources
from pathlib import Path

from fastapi.testclient import TestClient
from omniagents_workplace.server import Workplace, create_app


def main():
    package = resources.files("omniagents_workplace")
    for path in (
        "static/index.html",
        "canvas/support.js",
        "github/staging_check.sh",
        "reference/github_mcp_tools.json",
        "reference/slack_mcp_tools.json",
        "reference/linear_mcp_tools.json",
        "reference/canvas_mcp_tools.json",
        "seeds/acme.yml",
        "seeds/acme/repos/api-service/.github/workflows/tests.yml",
    ):
        assert package.joinpath(path).is_file(), f"Missing packaged resource: {path}"
    for path in package.joinpath("reference").iterdir():
        if path.name.endswith(".json"):
            json.loads(path.read_text())
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from omniagents_workplace.cli import main; main()",
            "--help",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with tempfile.TemporaryDirectory(prefix="workplace-package-") as directory:
        workplace = Workplace(
            Path(directory), base_url="http://testserver", run_workflows=False
        )
        try:
            with TestClient(create_app(workplace)) as client:
                for route in ("/", "/desktop", "/library"):
                    response = client.get(route)
                    assert response.status_code == 200, (route, response.text)
                    assert "text/html" in response.headers["content-type"]
                assets = list(package.joinpath("static/assets").iterdir())
                assert any(path.name.endswith(".js") for path in assets)
                assert any(path.name.endswith(".css") for path in assets)
                for path in assets:
                    if path.is_file():
                        response = client.get("/assets/" + path.name)
                        assert response.status_code == 200, path.name
                        assert response.content == path.read_bytes(), path.name
        finally:
            workplace.close()
    print(
        "Installed Workplace passed: CLI, UI routes/assets, reference schemas, and seed workflows."
    )


if __name__ == "__main__":
    main()
