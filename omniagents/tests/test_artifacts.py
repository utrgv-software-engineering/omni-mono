from omniagents.core.artifacts import set_artifact_callback, emit_artifact, display_artifact


def test_emit_artifact_callback():
    records = []
    set_artifact_callback(lambda t, c, m: records.append((t, c, m)))
    emit_artifact("title", "body", "markdown")
    assert records == [("title", "body", "markdown")]


def test_display_artifact_tool():
    records = []
    set_artifact_callback(lambda t, c, m: records.append((t, c, m)))
    display_artifact("a", "b", "html")
    assert records == [("a", "b", "html")]
