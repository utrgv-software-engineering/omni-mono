import argparse
import shutil
from pathlib import Path


def _copy_tree(src: Path, dest: Path) -> None:
  if dest.exists():
    raise RuntimeError(f'destination already exists: {dest}')
  shutil.copytree(src, dest)


def main() -> int:
  parser = argparse.ArgumentParser()
  parser.add_argument('--project-root', required=True)
  args = parser.parse_args()

  skill_root = Path(__file__).resolve().parents[1]
  template_root = skill_root / 'assets' / 'acceptance'

  if not template_root.exists():
    raise RuntimeError(f'missing template assets: {template_root}')

  project_root = Path(args.project_root).resolve()
  if not project_root.exists():
    raise RuntimeError(f'project root does not exist: {project_root}')

  dest = project_root / 'acceptance'
  _copy_tree(template_root, dest)

  print(str(dest))
  return 0


if __name__ == '__main__':
  raise SystemExit(main())
