import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

from omniagents.core.utils import jinja
from omniagents.core.utils import jinja_instructions


def test_render_template_renders_with_kwargs():
    result = jinja.render_template("Hello {{ name }}", name="World")
    assert result == "Hello World"


def test_render_template_returns_original_on_error(monkeypatch):
    def broken_env(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(jinja_instructions, "_create_env", broken_env)
    result = jinja_instructions.render_template("{{ value }}")
    assert result == "{{ value }}"


def test_render_template_silent_undefined(monkeypatch):
    result = jinja_instructions.render_template("{{ missing }}", silent_undefined=True)
    assert result == ""


def test_read_text_file_prefers_absolute(tmp_path):
    target = tmp_path / "data.txt"
    target.write_text("payload", encoding="utf-8")
    result = jinja_instructions.read_text_file(tmp_path, str(target))
    assert result == "payload"


def test_read_text_file_resolves_relative(tmp_path):
    target = tmp_path / "nested"
    target.mkdir()
    file_path = target / "data.txt"
    file_path.write_text("value", encoding="utf-8")
    result = jinja_instructions.read_text_file(tmp_path, "nested/data.txt")
    assert result == "value"


def test_read_text_file_missing_returns_empty(tmp_path):
    result = jinja_instructions.read_text_file(tmp_path, "missing.txt")
    assert result == ""


@dataclass
class SampleContext:
    value: int


class FakeModel:
    def model_dump(self):
        return {"payload": 1}


class LegacyModel:
    def dict(self):
        return {"legacy": 2}


class PlainObject:
    def __init__(self):
        self.field = "data"


def test_context_to_dict_handles_types():
    assert jinja_instructions.context_to_dict(None) == {}
    data = {"alpha": 1}
    assert jinja_instructions.context_to_dict(data) is data
    assert jinja_instructions.context_to_dict(SampleContext(3)) == {"value": 3}
    assert jinja_instructions.context_to_dict(FakeModel()) == {"payload": 1}
    assert jinja_instructions.context_to_dict(LegacyModel()) == {"legacy": 2}
    assert jinja_instructions.context_to_dict(PlainObject()) == {"field": "data"}
    assert jinja_instructions.context_to_dict(4) == {"context": 4}


def test_create_jinja2_instructions_renders_with_context():
    renderer = jinja_instructions.create_jinja2_instructions(
        "{{ context.name }} {{ missing }}"
    )
    context_wrapper = SimpleNamespace(context={"name": "Neo"})
    result = asyncio.run(renderer(context_wrapper, object()))
    assert result.strip() == "Neo"


def test_process_instructions_with_string(tmp_path):
    processor = jinja_instructions.process_instructions("Hello {{ context.name }}")
    context_wrapper = SimpleNamespace(context={"name": "Trinity"})
    result = asyncio.run(processor(context_wrapper, object()))
    assert result == "Hello Trinity"


def test_process_instructions_wraps_callable():
    def provider(context_wrapper, agent):
        return "Greetings {{ context.name }}"

    processor = jinja_instructions.process_instructions(provider)
    context_wrapper = SimpleNamespace(context={"name": "Morpheus"})
    result = asyncio.run(processor(context_wrapper, object()))
    assert result == "Greetings Morpheus"


def test_process_instructions_returns_non_string_values():
    def provider(context_wrapper, agent):
        return {"value": 5}

    processor = jinja_instructions.process_instructions(provider)
    result = asyncio.run(processor(SimpleNamespace(context=None), object()))
    assert result == {"value": 5}


def test_process_instructions_none_returns_none():
    assert jinja_instructions.process_instructions(None) is None
