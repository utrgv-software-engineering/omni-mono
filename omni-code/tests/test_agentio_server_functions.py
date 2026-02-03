import pytest


@pytest.mark.asyncio
async def test_good_accepts_empty_text():
    from server_functions.agentio_functions import good

    class FakeSession:
        id = "sess-1"

    result = await good(FakeSession(), "")
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_bad_accepts_empty_text():
    from server_functions.agentio_functions import bad

    class FakeSession:
        id = "sess-1"

    result = await bad(FakeSession(), "")
    assert result["ok"] is True

