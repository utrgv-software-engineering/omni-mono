import pytest

from omniagents.core.evaluation.registry import (
    _MEASURES,
    DuplicateMeasureError,
    InvalidMeasureError,
    UnknownMeasureError,
    evaluation_measure,
    get_measure,
    run_measures,
)


@pytest.fixture(autouse=True)
def reset_registry():
    original = dict(_MEASURES)
    _MEASURES.clear()
    try:
        yield
    finally:
        _MEASURES.clear()
        _MEASURES.update(original)


def test_evaluation_measure_registers_and_sets_metadata():
    @evaluation_measure
    def sample(ctx):
        return {"passed": True}

    registered = get_measure("sample")
    assert registered is sample
    assert getattr(sample, "_is_omni_evaluation_measure") is True
    assert getattr(sample, "_measure_name") == "sample"


def test_evaluation_measure_rejects_duplicates():
    @evaluation_measure(name="one")
    def one(ctx):
        return {"passed": True}

    with pytest.raises(DuplicateMeasureError):

        @evaluation_measure(name="one")
        def duplicate(ctx):
            return {"passed": False}


def test_get_measure_raises_for_unknown():
    with pytest.raises(UnknownMeasureError):
        get_measure("missing")


def test_run_measures_validates_requested_names():
    @evaluation_measure(name="alpha")
    def alpha(ctx):
        return {"passed": True}

    with pytest.raises(UnknownMeasureError):
        run_measures(object(), measures=["alpha", "beta"])


def test_run_measures_requires_dict_return():
    @evaluation_measure(name="gamma")
    def gamma(ctx):
        return ["invalid"]

    with pytest.raises(InvalidMeasureError):
        run_measures(object(), measures=["gamma"])


def test_run_measures_returns_named_results():
    @evaluation_measure(name="delta")
    def delta(ctx):
        return {"passed": False, "reason": "nope"}

    results = run_measures(object(), measures=["delta"])
    assert results == [{"passed": False, "reason": "nope", "name": "delta"}]
