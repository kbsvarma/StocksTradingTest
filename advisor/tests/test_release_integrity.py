from __future__ import annotations

from advisor.release_integrity import tree_digest


def test_tree_digest_is_stable_and_content_sensitive(tmp_path):
    (tmp_path / "a.py").write_text("one")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "runtime.json").write_text("ignored")
    first = tree_digest(tmp_path)
    (tmp_path / "data" / "runtime.json").write_text("still ignored")
    assert tree_digest(tmp_path) == first
    (tmp_path / "a.py").write_text("two")
    assert tree_digest(tmp_path) != first
