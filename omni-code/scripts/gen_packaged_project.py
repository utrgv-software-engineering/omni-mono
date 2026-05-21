#!/usr/bin/env python3
"""Generate the packaged omni_code/project.yml from the repo-root project.yml.

The packaged copy ships INSIDE the omni_code package (site-packages/omni_code/),
but agents/tools/server_functions/capabilities install as top-level packages one
level up (site-packages/). So the packaged project.yml's `paths` must point at
`../<dir>`. The repo-root project.yml keeps bare paths for editable/dev installs,
where the project root IS the repo root (the dirs are siblings of project.yml).

Usage: gen_packaged_project.py <src project.yml> <dest project.yml>
"""

import re
import sys

# The keys under `paths:` whose values are package dirs one level up in a wheel.
_PATH_KEYS = ("agents", "tools", "server_functions", "guardrails", "capabilities", "evaluations", "target")


def rewrite(text: str) -> str:
    pattern = re.compile(r"(\n  (?:" + "|".join(_PATH_KEYS) + r"): )(?!\.\.|/)")
    return pattern.sub(r"\1../", text)


def main() -> None:
    src, dest = sys.argv[1], sys.argv[2]
    with open(src) as f:
        text = f.read()
    with open(dest, "w") as f:
        f.write(rewrite(text))


if __name__ == "__main__":
    main()
