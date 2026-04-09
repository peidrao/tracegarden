"""tracegarden Celery integration."""
from .signals import connect_signals, disconnect_signals

__all__ = ["connect_signals", "disconnect_signals", "init_celery"]


def init_celery(storage=None, redactor=None) -> None:
    """Convenience helper to connect TraceGarden Celery signals."""
    connect_signals(storage=storage, redactor=redactor)
