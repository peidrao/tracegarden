"""
Tests for Celery runtime isolation.

The _CeleryRuntime class replaces bare module-level globals to ensure that
configure_runtime() calls in one test do not bleed into another, and that
concurrent configure calls are safe.
"""
import threading

from tracegarden.core.redaction import Redactor
from tracegarden.core.storage import TraceStorage
from tracegarden.integrations.celery import signals


def test_configure_runtime_replaces_storage(tmp_path):
    """configure_runtime sets a new storage instance atomically."""
    storage_a = TraceStorage(db_path=str(tmp_path / "a.db"))
    storage_b = TraceStorage(db_path=str(tmp_path / "b.db"))

    signals.configure_runtime(storage=storage_a)
    assert signals._runtime.get_storage() is storage_a

    signals.configure_runtime(storage=storage_b)
    assert signals._runtime.get_storage() is storage_b


def test_configure_runtime_replaces_redactor(tmp_path):
    redactor_a = Redactor()
    redactor_b = Redactor(param_denylist={"secret"})

    signals.configure_runtime(redactor=redactor_a)
    assert signals._runtime.get_redactor() is redactor_a

    signals.configure_runtime(redactor=redactor_b)
    assert signals._runtime.get_redactor() is redactor_b


def test_runtime_reset_clears_dependencies(tmp_path):
    """reset() clears stored dependencies so get_storage/get_redactor create fresh ones."""
    storage = TraceStorage(db_path=str(tmp_path / "reset.db"))
    signals.configure_runtime(storage=storage)
    assert signals._runtime.get_storage() is storage

    signals._runtime.reset()

    # After reset, a fresh TraceStorage is lazily created (not the same instance)
    fresh = signals._runtime.get_storage()
    assert fresh is not storage


def test_configure_runtime_partial_update_keeps_existing(tmp_path):
    """Passing only storage must not clear a previously configured redactor."""
    storage = TraceStorage(db_path=str(tmp_path / "partial.db"))
    redactor = Redactor()

    signals.configure_runtime(storage=storage, redactor=redactor)
    signals.configure_runtime(storage=TraceStorage(db_path=str(tmp_path / "new.db")))

    # Redactor must be unchanged
    assert signals._runtime.get_redactor() is redactor


def test_concurrent_configure_does_not_corrupt(tmp_path):
    """Concurrent configure_runtime calls must not corrupt the runtime state."""
    errors: list[str] = []

    def worker(i: int) -> None:
        storage = TraceStorage(db_path=str(tmp_path / f"worker_{i}.db"))
        redactor = Redactor()
        try:
            signals.configure_runtime(storage=storage, redactor=redactor)
            # Immediately read back — must not raise or return None
            s = signals._runtime.get_storage()
            r = signals._runtime.get_redactor()
            if s is None or r is None:
                errors.append(f"worker {i}: got None")
        except Exception as exc:
            errors.append(f"worker {i}: {exc}")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Concurrent errors: {errors}"
