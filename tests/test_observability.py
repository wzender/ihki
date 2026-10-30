import logging

from app.observability import TraceContextFilter, configure_tracing, rum_snippet


def test_rum_is_absent_without_public_application_token():
    assert rum_snippet("", "production", "v1", False) == ""


def test_rum_loader_has_release_and_private_identity_is_opt_in():
    html = rum_snippet("public-token", "staging", "v1", False)
    assert "https://betterstack.net/b.js?t=" in html
    assert '"public-token"' in html and 'environment:"staging"' in html and 'release:"v1"' in html
    assert "identifyUsers:false" in html


def test_rum_values_cannot_close_the_script_element():
    html = rum_snippet("x</script><script>alert(1)</script>", "production", "v1", True)
    assert "</script><script>" not in html
    assert "identifyUsers:true" in html


def test_tracing_is_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv("BETTER_STACK_SOURCE_TOKEN", raising=False)
    monkeypatch.delenv("BETTER_STACK_INGESTING_HOST", raising=False)
    assert configure_tracing(object()) is False


def test_log_filter_is_safe_outside_a_span():
    assert TraceContextFilter().filter(logging.LogRecord("test", logging.INFO, __file__, 1, "ok", (), None))
