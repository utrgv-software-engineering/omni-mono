"""Distribution builds must contain the prebuilt Workplace UI.

The sdist carries these assets too, so installing it never needs Node or the
monorepo. Editable installs remain usable for backend/frontend development.
"""

from pathlib import Path
from html.parser import HTMLParser

from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist
from setuptools.errors import SetupError


def check_ui():
    root = Path(__file__).parent / "omniagents_workplace" / "static"
    index = root / "index.html"
    if not index.is_file():
        raise SetupError(
            "Workplace UI is missing. From the monorepo checkout run "
            "python omniagents-workplace/scripts/build_package.py. "
            "Published source distributions must include the built UI."
        )

    class Assets(HTMLParser):
        paths = []

        def handle_starttag(self, tag, attrs):
            for name, value in attrs:
                if name in {"src", "href"} and value and value.startswith("/assets/"):
                    self.paths.append(value.lstrip("/"))

    parser = Assets()
    parser.feed(index.read_text(encoding="utf-8"))
    if not parser.paths or any(not (root / path).is_file() for path in parser.paths):
        raise SetupError(
            "Workplace UI assets are incomplete; rebuild with scripts/build_package.py"
        )


class BuildPy(build_py):
    def run(self):
        if not self.editable_mode:
            check_ui()
        super().run()


class Sdist(sdist):
    def run(self):
        check_ui()
        super().run()


setup(cmdclass={"build_py": BuildPy, "sdist": Sdist})
