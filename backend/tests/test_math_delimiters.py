from app.logic.export import render_markdown
from app.logic.text import normalize_math_delimiters


def test_model_formulas_render_as_math_in_offline_export():
    text = r"页数 \(20000/40=500\)。" + "\n\n" + r"\[40+\lceil40/10\rceil\times500=2040\]"
    rendered = render_markdown(text, "example")
    assert rendered.count("<math") == 2
    assert "2040" in rendered and "500" in rendered


def test_code_samples_and_dollar_math_are_preserved():
    for text in [r"`\(code\)`", "```tex\n\\[code\\]\n```", "~~~tex\n\\(code\\)\n~~~", r"$\text{\(literal\)}$", r"$$\text{\[literal\]}$$"]:
        assert normalize_math_delimiters(text) == text
    assert "<math" not in render_markdown(r"`\(code\)`", "code")


def test_incomplete_math_and_note_references_are_unchanged():
    text = r"未完成 \(x+1 与 [[笔记]]、[出处](/materials/view?id=abc)"
    assert normalize_math_delimiters(text) == text
