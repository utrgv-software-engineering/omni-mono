import importlib
from omniagents.core.session import Session
import omniagents.core.session.history_db as history_db_module


def test_local_session_persistence(tmp_path):
    # env and module reload handled by fixture
    sess = Session()
    msg = {"role": "user", "content": "hi"}
    sess.append_message(msg)

    # Reload to simulate new process
    importlib.reload(history_db_module)
    loaded = Session(sess.id)
    assert loaded.history == [msg]
