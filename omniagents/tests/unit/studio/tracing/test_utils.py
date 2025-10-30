import pytest

from omniagents.studio.tracing import utils


def test_generate_readable_name_avoids_collisions(monkeypatch):
    choices = iter(["brave", "trace"])

    def fake_choice(seq):
        return next(choices)

    monkeypatch.setattr(utils.random, "choice", fake_choice)
    name = utils.generate_readable_name(["brave-trace"])
    assert name == "brave-trace-0"


def test_generate_readable_name_returns_base(monkeypatch):
    choices = iter(["calm", "agent"])

    def fake_choice(seq):
        return next(choices)

    monkeypatch.setattr(utils.random, "choice", fake_choice)
    name = utils.generate_readable_name([])
    assert name == "calm-agent"


def test_fibo_sequence_terminates():
    gen = utils.fibo()
    values = [next(gen) for _ in range(8)]
    assert values == [1, 1, 2, 3, 5, 8, 13, 21]
    while True:
        value = next(gen)
        if value is None:
            break
    with pytest.raises(StopIteration):
        next(gen)


def test_validate_keys_disallows_reserved_and_private():
    with pytest.raises(ValueError):
        utils.validate_keys({"trace_id": "x"})
    with pytest.raises(ValueError):
        utils.validate_keys({"__secret": "x"})
    utils.validate_keys({"allowed": "value"})
