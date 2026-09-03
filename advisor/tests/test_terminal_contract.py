from pathlib import Path


def test_terminal_toast_icons_are_valid_for_streamlit_runtime():
    """Regression: an invalid success icon raised after queuing a real run."""
    import ast
    from streamlit.string_util import validate_icon_or_emoji

    source = Path(__file__).resolve().parents[1] / "terminal.py"
    tree = ast.parse(source.read_text())
    icons = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "toast":
            for kw in node.keywords:
                if kw.arg == "icon" and isinstance(kw.value, ast.Constant):
                    icons.append(kw.value.value)
    assert icons
    for icon in icons:
        assert validate_icon_or_emoji(icon) == icon
