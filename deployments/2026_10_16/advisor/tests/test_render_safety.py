from advisor.render_safety import http_url, secret_equal, text


def test_html_text_is_escaped():
    payload = '<img src=x onerror="steal()">'
    rendered = text(payload)
    assert "<img" not in rendered
    assert "onerror=\"" not in rendered


def test_http_url_is_validated_and_attribute_escaped():
    assert http_url("javascript:alert(1)") is None
    assert http_url("https://") is None
    rendered = http_url('https://example.com/\" onmouseover=\"steal()')
    assert rendered is not None
    assert '" onmouseover="' not in rendered
    assert "&quot;" in rendered


def test_secret_comparison_rejects_nonstrings_and_mismatch():
    assert secret_equal("same", "same")
    assert not secret_equal("wrong", "same")
    assert not secret_equal(["same"], "same")
