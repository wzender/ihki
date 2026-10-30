import logging

from app.observability import TraceContextFilter, configure_tracing, current_traceparent, rum_snippet


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


def test_current_traceparent_formats_active_span(monkeypatch):
    class Context:
        is_valid = True
        trace_id = 0x0AF7651916CD43DD8448EB211C80319C
        span_id = 0xB7AD6B7169203331
        trace_flags = 1

    class Span:
        @staticmethod
        def get_span_context():
            return Context()

    monkeypatch.setattr("opentelemetry.trace.get_current_span", lambda: Span())
    assert current_traceparent() == "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def test_current_traceparent_is_empty_without_valid_span(monkeypatch):
    class Span:
        @staticmethod
        def get_span_context():
            return type("Context", (), {"is_valid": False})()

    monkeypatch.setattr("opentelemetry.trace.get_current_span", lambda: Span())
    assert current_traceparent() == ""
