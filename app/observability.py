"""Optional Better Stack tracing and log correlation.

The application remains fully functional when OpenTelemetry is not installed or
when Better Stack credentials are absent.  RUM is configured separately in the
HTML response because its application token is intentionally public.
"""
from __future__ import annotations

import logging
import json
import os
import threading


_lock = threading.Lock()
_provider_configured = False


def rum_snippet(token: str, environment: str, release: str, identify_users: bool) -> str:
    """Return the public Better Stack browser loader, or nothing when disabled."""
    token = token.strip()
    if not token:
        return ""

    # Escaping '<' prevents an environment value from terminating the script element.
    js = lambda value: json.dumps(value).replace("<", "\\u003c")
    return f"""<script>
!function(b,e,t,r){{b[t]=b[t]||function(...a){{(b[t].q=b[t].q||[]).push(a)}};b[t].l=+new Date;
var s=e.createElement('script');s.async=1;s.crossOrigin='anonymous';s.src='https://betterstack.net/b.js?t='+r;
(e.head||e.getElementsByTagName('head')[0]).appendChild(s)}}(window,document,'betterstack',{js(token)});
betterstack('init',{{environment:{js(environment)},release:{js(release)}}});
window.IHKI_RUM={{enabled:true,identifyUsers:{js(identify_users)}}};
</script>"""


class TraceContextFilter(logging.Filter):
    """Add Better Stack's expected trace fields to records inside a span."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            from opentelemetry import trace

            context = trace.get_current_span().get_span_context()
            if context.is_valid:
                record.span = {
                    "trace_id": format(context.trace_id, "032x"),
                    "span_id": format(context.span_id, "016x"),
                }
        except Exception:
            # Logging must never fail because optional observability is unavailable.
            pass
        return True


def configure_tracing(app, *, token: str = "", host: str = "") -> bool:
    """Instrument a FastAPI app and export spans to Better Stack when configured."""
    token = (token or os.environ.get("BETTER_STACK_SOURCE_TOKEN", "")).strip()
    host = (host or os.environ.get("BETTER_STACK_INGESTING_HOST", "")).strip()
    if not token or not host:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger("ihki").warning("Better Stack tracing disabled: OpenTelemetry packages are not installed")
        return False

    endpoint = host if host.startswith(("https://", "http://")) else f"https://{host}"
    endpoint = endpoint.rstrip("/") + "/v1/traces"

    global _provider_configured
    with _lock:
        if not _provider_configured:
            provider = TracerProvider(resource=Resource.create({"service.name": "ihki"}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(
                endpoint=endpoint,
                headers={"Authorization": f"Bearer {token}"},
            )))
            trace.set_tracer_provider(provider)
            _provider_configured = True

    # Exclude static assets and health polling: they add volume but little diagnostic value.
    FastAPIInstrumentor.instrument_app(app, excluded_urls=r"/static/.*,/api/health,/service-worker\.js")
    return True
