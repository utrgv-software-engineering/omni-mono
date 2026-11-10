"""Who read what, in a Confidential factory: conversations, transcripts and files."""

from types import SimpleNamespace

from conftest import signed_in
from test_shared import upload


def test_reads_are_recorded_in_confidential_factories_once_an_hour(
    workplace_server, monkeypatch
):
    w, url = workplace_server
    g = w.governance
    reader = SimpleNamespace(username="priya")
    g.record_access(reader, "conversation", "c1")
    assert not w.audit_log.events(event="access.conversation")  # not Confidential
    w.store.set_meta("factory.profile", {"classification": "confidential"})
    now = [1000.0]
    import time

    monkeypatch.setattr(time, "time", lambda: now[0])
    g.record_access(reader, "conversation", "c1")
    g.record_access(reader, "conversation", "c1")
    g.record_access(SimpleNamespace(username="lee"), "conversation", "c1")
    now[0] += g.ACCESS_INTERVAL
    g.record_access(reader, "conversation", "c1")
    events = w.audit_log.events(event="access.conversation")
    assert [(e["actor"], e["target"]) for e in events] == [
        ("priya", "c1"),
        ("lee", "c1"),
        ("priya", "c1"),
    ]


def test_a_shared_download_is_a_recorded_read(workplace_server):
    w, url = workplace_server
    w.store.set_meta("factory.profile", {"classification": "confidential"})
    with signed_in(w, url) as user:
        upload(user, {"Extracts/cohort.csv": b"mrn,name\n"})
        assert (
            user.get(
                "/workplace/api/shared/content?path=Extracts/cohort.csv"
            ).status_code
            == 200
        )
        assert (
            user.get("/workplace/api/shared/download-folder?path=Extracts").status_code
            == 200
        )
    reads = [
        (e["event"], e["target"])
        for e in w.audit_log.events()
        if e["event"].startswith("access.")
    ]
    assert reads == [
        ("access.file", "Extracts/cohort.csv"),
        ("access.folder", "Extracts"),
    ]
