from types import SimpleNamespace

from tracegarden.core.context import clear_request_context
from tracegarden.core.storage import TraceStorage
from tracegarden.integrations.celery import signals


def test_before_publish_skips_without_parent_trace(tmp_path, monkeypatch):
    clear_request_context()
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    monkeypatch.setattr(signals, "_get_storage", lambda: storage)

    headers = {"id": "task-1", "task": "demo.task"}
    signals._on_before_task_publish(headers=headers, body=([1], {"a": 1}), routing_key="celery")

    assert storage.get_task_by_celery_id("task-1") is None


def test_before_publish_and_prerun_with_parent_trace(tmp_path, monkeypatch):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    monkeypatch.setattr(signals, "_get_storage", lambda: storage)

    headers = {
        "id": "task-2",
        "task": "demo.task",
        signals._TRACEGARDEN_PARENT_KEY: "a" * 32,
    }
    signals._on_before_task_publish(headers=headers, body=([1], {"a": 1}), routing_key="celery")

    task = storage.get_task_by_celery_id("task-2")
    assert task is not None
    assert task.parent_trace_id == "a" * 32

    fake_task = SimpleNamespace(name="demo.task", request=SimpleNamespace(headers=headers, delivery_info={}))
    signals._on_task_prerun(task_id="task-2", task=fake_task, args=[1], kwargs={"a": 1})

    updated = storage.get_task_by_celery_id("task-2")
    assert updated is not None
    assert updated.state == "STARTED"


def test_celery_payload_is_redacted(tmp_path, monkeypatch):
    storage = TraceStorage(db_path=str(tmp_path / "tg.db"))
    monkeypatch.setattr(signals, "_get_storage", lambda: storage)

    headers = {
        "id": "task-3",
        "task": "demo.task",
        signals._TRACEGARDEN_PARENT_KEY: "b" * 32,
    }
    signals._on_before_task_publish(
        headers=headers,
        body=(["ok"], {"password": "123", "token": "abc"}),
        routing_key="celery",
    )

    task = storage.get_task_by_celery_id("task-3")
    assert task is not None
    assert task.kwargs["password"] == "[REDACTED]"
    assert task.kwargs["token"] == "[REDACTED]"
