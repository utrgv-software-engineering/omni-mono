"""remove_tree deletes trees that hold read-only files."""

from __future__ import annotations

import os
import stat

from omniagents.core.utils.fs import remove_tree


def test_remove_tree_deletes_read_only_files(tmp_path):
    # git marks object files read-only; Windows will not delete those as-is.
    tree = tmp_path / "repo"
    objects = tree / ".git" / "objects" / "ab"
    objects.mkdir(parents=True)
    blob = objects / "cdef"
    blob.write_bytes(b"blob")
    os.chmod(blob, stat.S_IREAD)
    remove_tree(tree)
    assert not tree.exists()


def test_remove_tree_can_ignore_a_missing_tree(tmp_path):
    remove_tree(tmp_path / "missing", ignore_errors=True)
