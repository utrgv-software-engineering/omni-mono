import json
import os
import pytest
from omniagents.builtin.tools.notebook_tools import read_notebook, edit_notebook


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_notebook(cells, nbformat=4, nbformat_minor=5):
    """Build a minimal v4 notebook dict."""
    return {
        "nbformat": nbformat,
        "nbformat_minor": nbformat_minor,
        "metadata": {},
        "cells": cells,
    }


def _code_cell(source, outputs=None, cell_id=None):
    cell = {
        "cell_type": "code",
        "source": source.splitlines(True),
        "metadata": {},
        "outputs": outputs or [],
        "execution_count": None,
    }
    if cell_id:
        cell["id"] = cell_id
    return cell


def _md_cell(source, cell_id=None):
    cell = {
        "cell_type": "markdown",
        "source": source.splitlines(True),
        "metadata": {},
    }
    if cell_id:
        cell["id"] = cell_id
    return cell


def _write_nb(path, nb):
    with open(path, "w") as f:
        json.dump(nb, f)


def _read_nb(path):
    with open(path) as f:
        return json.load(f)


# ===========================================================================
# read_notebook tests
# ===========================================================================


class TestReadNotebook:

    def test_read_basic_notebook(self, invoke_tool, tmp_path):
        """Read a notebook with code and markdown cells."""
        nb = _make_notebook([
            _md_cell("# Hello", cell_id="md1"),
            _code_cell("print('hi')", cell_id="c1"),
        ])
        nb_path = str(tmp_path / "test.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(read_notebook, file_path=nb_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert meta["cell_count"] == 2
        assert "# Hello" in result.ui_metadata["value"]
        assert "print('hi')" in result.ui_metadata["value"]

    def test_read_cell_outputs(self, invoke_tool, tmp_path):
        """Code cell outputs (stream, execute_result, error) are included."""
        outputs = [
            {"output_type": "stream", "name": "stdout", "text": ["hello world\n"]},
            {
                "output_type": "execute_result",
                "data": {"text/plain": ["42"]},
                "metadata": {},
                "execution_count": 1,
            },
            {
                "output_type": "error",
                "ename": "ValueError",
                "evalue": "bad value",
                "traceback": [],
            },
        ]
        nb = _make_notebook([_code_cell("x = 1", outputs=outputs)])
        nb_path = str(tmp_path / "out.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(read_notebook, file_path=nb_path)
        value = result.ui_metadata["value"]

        assert "hello world" in value
        assert "42" in value
        assert "ValueError: bad value" in value

    def test_read_display_data_markers(self, invoke_tool, tmp_path):
        """HTML and image outputs produce placeholder markers."""
        outputs = [
            {
                "output_type": "display_data",
                "data": {"text/html": ["<b>bold</b>"], "image/png": "abc123"},
                "metadata": {},
            },
        ]
        nb = _make_notebook([_code_cell("display()", outputs=outputs)])
        nb_path = str(tmp_path / "disp.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(read_notebook, file_path=nb_path)
        value = result.ui_metadata["value"]

        assert "[HTML output]" in value
        assert "[Image output]" in value

    def test_read_empty_notebook(self, invoke_tool, tmp_path):
        """An empty notebook returns a friendly message."""
        nb = _make_notebook([])
        nb_path = str(tmp_path / "empty.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(read_notebook, file_path=nb_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert meta["cell_count"] == 0
        assert "empty" in result.ui_metadata["value"].lower()

    def test_read_file_not_found(self, invoke_tool):
        """Non-existent file returns an error."""
        result = invoke_tool(read_notebook, file_path="/no/such/file.ipynb")
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is False
        assert meta["error_type"] == "file_not_found"

    def test_read_non_ipynb_extension(self, invoke_tool, tmp_path):
        """A file that isn't .ipynb is rejected."""
        txt_path = str(tmp_path / "notes.txt")
        with open(txt_path, "w") as f:
            f.write("hello")

        result = invoke_tool(read_notebook, file_path=txt_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is False
        assert meta["error_type"] == "invalid_file_type"

    def test_read_invalid_json(self, invoke_tool, tmp_path):
        """A .ipynb file with broken JSON returns a parse error."""
        nb_path = str(tmp_path / "bad.ipynb")
        with open(nb_path, "w") as f:
            f.write("{not json!!")

        result = invoke_tool(read_notebook, file_path=nb_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is False
        assert meta["error_type"] == "parse_error"

    def test_read_truncated_preview(self, invoke_tool, tmp_path):
        """Long content produces a truncated preview."""
        long_text = "x" * 1000
        nb = _make_notebook([_md_cell(long_text)])
        nb_path = str(tmp_path / "long.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(read_notebook, file_path=nb_path)

        assert result.ui_metadata["truncated"] is True
        assert len(result.ui_metadata["preview"]) == 500

    def test_read_v3_notebook(self, invoke_tool, tmp_path):
        """Handles older notebook format (worksheets)."""
        nb = {
            "nbformat": 3,
            "nbformat_minor": 0,
            "metadata": {},
            "worksheets": [
                {
                    "cells": [
                        {
                            "cell_type": "markdown",
                            "source": ["# Old format"],
                            "metadata": {},
                        }
                    ]
                }
            ],
        }
        nb_path = str(tmp_path / "v3.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(read_notebook, file_path=nb_path)
        meta = result.ui_metadata["metadata"]

        assert meta["success"] is True
        assert meta["cell_count"] == 1
        assert "Old format" in result.ui_metadata["value"]


# ===========================================================================
# edit_notebook – replace mode
# ===========================================================================


class TestEditNotebookReplace:

    def test_replace_cell_content(self, invoke_tool, tmp_path):
        """Replace a cell's source content."""
        nb = _make_notebook([_code_cell("old code"), _md_cell("# Title")])
        nb_path = str(tmp_path / "edit.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="new code",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is True

        saved = _read_nb(nb_path)
        assert "".join(saved["cells"][0]["source"]) == "new code"

    def test_replace_clears_outputs(self, invoke_tool, tmp_path):
        """Replacing a code cell clears its outputs and execution_count."""
        outputs = [{"output_type": "stream", "name": "stdout", "text": ["hi"]}]
        nb = _make_notebook([_code_cell("old", outputs=outputs)])
        nb["cells"][0]["execution_count"] = 5
        nb_path = str(tmp_path / "clear.ipynb")
        _write_nb(nb_path, nb)

        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="new",
        )

        saved = _read_nb(nb_path)
        assert saved["cells"][0]["outputs"] == []
        assert saved["cells"][0]["execution_count"] is None

    def test_replace_change_cell_type(self, invoke_tool, tmp_path):
        """Replace can also change the cell type."""
        nb = _make_notebook([_code_cell("x = 1")])
        nb_path = str(tmp_path / "retype.ipynb")
        _write_nb(nb_path, nb)

        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="# Now markdown",
            cell_type="markdown",
        )

        saved = _read_nb(nb_path)
        assert saved["cells"][0]["cell_type"] == "markdown"

    def test_replace_out_of_range(self, invoke_tool, tmp_path):
        """Index beyond the last cell returns an error."""
        nb = _make_notebook([_md_cell("only cell")])
        nb_path = str(tmp_path / "oob.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=5,
            new_source="nope",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "index_error"

    def test_replace_negative_index(self, invoke_tool, tmp_path):
        """Negative cell_number returns an error."""
        nb = _make_notebook([_md_cell("cell")])
        nb_path = str(tmp_path / "neg.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=-1,
            new_source="nope",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "index_error"


# ===========================================================================
# edit_notebook – insert mode
# ===========================================================================


class TestEditNotebookInsert:

    def test_insert_at_beginning(self, invoke_tool, tmp_path):
        """Insert a cell at index 0."""
        nb = _make_notebook([_md_cell("existing")])
        nb_path = str(tmp_path / "ins.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="# New first cell",
            edit_mode="insert",
            cell_type="markdown",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is True
        assert meta["cell_count"] == 2

        saved = _read_nb(nb_path)
        assert "".join(saved["cells"][0]["source"]) == "# New first cell"
        assert "".join(saved["cells"][1]["source"]) == "existing"

    def test_insert_at_end(self, invoke_tool, tmp_path):
        """Insert a cell at the end (index == len(cells))."""
        nb = _make_notebook([_md_cell("first")])
        nb_path = str(tmp_path / "append.ipynb")
        _write_nb(nb_path, nb)

        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=1,
            new_source="print('last')",
            edit_mode="insert",
            cell_type="code",
        )

        saved = _read_nb(nb_path)
        assert len(saved["cells"]) == 2
        assert saved["cells"][1]["cell_type"] == "code"
        assert saved["cells"][1]["outputs"] == []
        assert saved["cells"][1]["execution_count"] is None

    def test_insert_code_cell_has_outputs_field(self, invoke_tool, tmp_path):
        """Inserted code cells get outputs and execution_count fields."""
        nb = _make_notebook([])
        nb_path = str(tmp_path / "empty_ins.ipynb")
        _write_nb(nb_path, nb)

        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="1 + 1",
            edit_mode="insert",
            cell_type="code",
        )

        saved = _read_nb(nb_path)
        cell = saved["cells"][0]
        assert "outputs" in cell
        assert "execution_count" in cell

    def test_insert_missing_cell_type(self, invoke_tool, tmp_path):
        """Insert without cell_type returns an error."""
        nb = _make_notebook([_md_cell("x")])
        nb_path = str(tmp_path / "notype.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="oops",
            edit_mode="insert",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "missing_cell_type"

    def test_insert_out_of_range(self, invoke_tool, tmp_path):
        """Insert index > len(cells) returns an error."""
        nb = _make_notebook([_md_cell("x")])
        nb_path = str(tmp_path / "ins_oob.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=10,
            new_source="nope",
            edit_mode="insert",
            cell_type="code",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "index_error"


# ===========================================================================
# edit_notebook – delete mode
# ===========================================================================


class TestEditNotebookDelete:

    def test_delete_cell(self, invoke_tool, tmp_path):
        """Delete a cell by index."""
        nb = _make_notebook([
            _md_cell("keep"),
            _code_cell("remove me"),
            _md_cell("also keep"),
        ])
        nb_path = str(tmp_path / "del.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=1,
            new_source="",
            edit_mode="delete",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is True
        assert meta["cell_count"] == 2

        saved = _read_nb(nb_path)
        sources = ["".join(c["source"]) for c in saved["cells"]]
        assert sources == ["keep", "also keep"]

    def test_delete_only_cell(self, invoke_tool, tmp_path):
        """Delete the only cell leaves an empty notebook."""
        nb = _make_notebook([_md_cell("alone")])
        nb_path = str(tmp_path / "solo.ipynb")
        _write_nb(nb_path, nb)

        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="",
            edit_mode="delete",
        )

        saved = _read_nb(nb_path)
        assert saved["cells"] == []

    def test_delete_out_of_range(self, invoke_tool, tmp_path):
        """Delete with index out of range returns an error."""
        nb = _make_notebook([_md_cell("x")])
        nb_path = str(tmp_path / "del_oob.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=3,
            new_source="",
            edit_mode="delete",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "index_error"


# ===========================================================================
# edit_notebook – general error handling
# ===========================================================================


class TestEditNotebookErrors:

    def test_file_not_found(self, invoke_tool):
        """Editing a non-existent file returns an error."""
        result = invoke_tool(
            edit_notebook,
            file_path="/no/such/notebook.ipynb",
            cell_number=0,
            new_source="x",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "file_not_found"

    def test_invalid_edit_mode(self, invoke_tool, tmp_path):
        """An unknown edit_mode returns an error."""
        nb = _make_notebook([_md_cell("x")])
        nb_path = str(tmp_path / "mode.ipynb")
        _write_nb(nb_path, nb)

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="x",
            edit_mode="append",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "invalid_edit_mode"

    def test_invalid_json_notebook(self, invoke_tool, tmp_path):
        """Editing a corrupt .ipynb returns a parse error."""
        nb_path = str(tmp_path / "corrupt.ipynb")
        with open(nb_path, "w") as f:
            f.write("not json")

        result = invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="x",
        )
        meta = result.ui_metadata["metadata"]
        assert meta["success"] is False
        assert meta["error_type"] == "parse_error"


# ===========================================================================
# Round-trip: read → edit → read
# ===========================================================================


class TestRoundTrip:

    def test_read_edit_read(self, invoke_tool, tmp_path):
        """Full round-trip: read, edit, read back to verify."""
        nb = _make_notebook([
            _md_cell("# Original"),
            _code_cell("x = 1"),
        ])
        nb_path = str(tmp_path / "rt.ipynb")
        _write_nb(nb_path, nb)

        # Read
        r1 = invoke_tool(read_notebook, file_path=nb_path)
        assert r1.ui_metadata["metadata"]["cell_count"] == 2

        # Edit cell 1
        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=1,
            new_source="x = 42",
        )

        # Insert cell at end
        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=2,
            new_source="print(x)",
            edit_mode="insert",
            cell_type="code",
        )

        # Delete original markdown header
        invoke_tool(
            edit_notebook,
            file_path=nb_path,
            cell_number=0,
            new_source="",
            edit_mode="delete",
        )

        # Read again
        r2 = invoke_tool(read_notebook, file_path=nb_path)
        assert r2.ui_metadata["metadata"]["cell_count"] == 2

        saved = _read_nb(nb_path)
        sources = ["".join(c["source"]) for c in saved["cells"]]
        assert sources == ["x = 42", "print(x)"]
