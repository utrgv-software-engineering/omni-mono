import importlib
from omniagents.core import local_sessions


def test_local_session_persistence(tmp_path):
    # env and module reload handled by fixture
    sess = local_sessions.LocalSession()
    msg = {'role': 'user', 'content': 'hi'}
    sess.append_message(msg)

    # Reload to simulate new process
    importlib.reload(local_sessions)
    loaded = local_sessions.LocalSession(sess.id)
    assert loaded.history == [msg]
