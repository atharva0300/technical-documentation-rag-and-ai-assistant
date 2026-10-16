import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

logger = logging.getLogger(__name__)

_tracer_initialized = False

def setup_telemetry(app=None):
    global _tracer_initialized
    if _tracer_initialized:
        return trace.get_tracer(__name__)

    try:
        provider = TracerProvider()
        processor = BatchSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

        if app:
            FastAPIInstrumentor.instrument_app(app)

        _tracer_initialized = True
        logger.info("OpenTelemetry tracing initialized successfully.")
    except Exception as e:
        logger.warning(f"Failed to initialize OpenTelemetry tracing: {e}")

    return trace.get_tracer("engineering_ai_assistant")

def get_tracer():
    return trace.get_tracer("engineering_ai_assistant")
