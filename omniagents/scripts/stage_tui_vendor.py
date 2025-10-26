#!/usr/bin/env python3

"""Stage prebuilt omni-tui binaries into the vendor directory."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from typing import Iterable, NamedTuple


class Artifact(NamedTuple):
    path: Path
    target: str
    suffix: str
    hashed: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stage prebuilt omni-tui binaries")
    parser.add_argument(
        "--artifacts",
        required=True,
        type=Path,
        help="Directory containing build artifacts",
    )
    parser.add_argument(
        "--vendor", required=True, type=Path, help="Target vendor directory"
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Remove existing staged binaries before copying",
    )
    return parser.parse_args()


def iter_artifacts(root: Path) -> Iterable[Artifact]:
    prefix = "omni-tui-"
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        name = path.name
        hashed = False
        base = name
        if base.endswith(".sha256"):
            hashed = True
            base = base[: -len(".sha256")]
        if not base.startswith(prefix):
            continue
        for suffix in (".tar.gz", ".zip"):
            if base.endswith(suffix):
                target = base[len(prefix) : -len(suffix)]
                yield Artifact(path=path, target=target, suffix=suffix, hashed=hashed)
                break


def ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def clear_vendor(vendor_root: Path) -> None:
    for item in vendor_root.iterdir():
        if item.is_dir():
            for child in item.iterdir():
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)
        elif item.is_file() and item.name != "README.md":
            item.unlink()


def stage_artifacts(artifacts: Iterable[Artifact], vendor_root: Path) -> int:
    staged = 0
    for artifact in artifacts:
        dest_dir = vendor_root / artifact.target
        ensure_directory(dest_dir)
        base_name = "omni-tui"
        dest_name = base_name + artifact.suffix
        if artifact.hashed:
            dest_name += ".sha256"
        dest_path = dest_dir / dest_name
        ensure_directory(dest_dir)
        shutil.copy2(artifact.path, dest_path)
        staged += 1
    return staged


def main() -> None:
    args = parse_args()
    artifacts_root = args.artifacts.resolve()
    vendor_root = args.vendor.resolve()
    ensure_directory(vendor_root)
    if args.clear:
        clear_vendor(vendor_root)
    artifacts = list(iter_artifacts(artifacts_root))
    if not artifacts:
        raise SystemExit("no omni-tui artifacts found")
    staged_count = stage_artifacts(artifacts, vendor_root)
    print(f"Staged {staged_count} artifacts into {vendor_root}")


if __name__ == "__main__":
    main()
