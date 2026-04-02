"""Distributed tracing helpers.

Wraps OpenTelemetry instrumentation in try/except ImportError
so the app works without the ADOT Lambda layer installed locally.
"""


def get_current_trace_id() -> str | None:
    """Extract current trace ID from OpenTelemetry span context."""
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx.trace_id == 0:
            return None
        return format(ctx.trace_id, "032x")
    except ImportError:
        return None


def instrument_app(app) -> None:
    """Instrument FastAPI app with OpenTelemetry auto-instrumentation."""
    try:
        from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        FastAPIInstrumentor.instrument_app(app)
        BotocoreInstrumentor().instrument()
        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass
