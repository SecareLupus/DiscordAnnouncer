from pathlib import Path

import pytest

from src.notifier import templates


def test_parse_var_assignments():
    result = templates.parse_var_assignments(["foo=bar", "answer=42"])
    assert result == {"foo": "bar", "answer": "42"}


@pytest.mark.parametrize("assignment", ["novalue", "=missing", "  =  "])
def test_parse_var_assignments_invalid(assignment):
    with pytest.raises(templates.TemplateRenderError):
        templates.parse_var_assignments([assignment])


def test_build_template_context_includes_defaults():
    env_values = {"BOT_USERNAME": "Alert Bot", "default_color": "0x123"}
    overrides = {"title": "Stream"}
    context = templates.build_template_context(
        message="hello",
        include_everyone=True,
        env_values=env_values,
        overrides=overrides,
    )
    assert context["message"] == "hello"
    assert context["message_prefix"].startswith("@everyone")
    assert context["BOT_USERNAME"] == "Alert Bot"
    assert context["DEFAULT_COLOR"] == "0x123"
    assert context["title"] == "Stream"
    assert "now_iso" in context


def test_render_template(tmp_path: Path):
    template_path = tmp_path / "payload.json.j2"
    template_path.write_text('{"content": "{{ message }}"}')

    context = {"message": "Hello"}
    payload = templates.render_template(template_path, context)
    assert payload["content"] == "Hello"

    # Ensure invalid JSON raises
    template_path.write_text("{{ message }}")
    with pytest.raises(templates.TemplateRenderError):
        templates.render_template(template_path, context)
