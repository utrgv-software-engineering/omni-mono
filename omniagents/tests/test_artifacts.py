from omniagents.core.tools.artifacts import set_artifact_callback, emit_artifact, display_artifact


def test_emit_artifact_callback():
    records = []
    set_artifact_callback(lambda t, c, m, aid: records.append((t, c, m, aid)))
    emit_artifact("title", "body", "markdown", artifact_id="id")
    assert records == [("title", "body", "markdown", "id")]


def test_display_artifact_tool():
    records = []
    set_artifact_callback(lambda t, c, m, aid: records.append((t, c, m, aid)))
    display_artifact("a", "b", "html", artifact_id="2")
    assert records == [("a", "b", "html", "2")]
