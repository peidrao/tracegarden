# Contributing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev,django,flask,fastapi,celery]'
```

## Run Tests

```bash
pytest -q
```

## Development Guidelines

- Keep framework integrations thin; put shared behavior in `tracegarden/core`.
- Preserve redaction-by-default for any new capture surface.
- Add/adjust tests for each behavior change.
- Avoid swallowing exceptions silently; use debug logs for non-fatal failures.

## Pull Request Checklist

- [ ] Tests updated and passing locally
- [ ] README/docs updated when API or behavior changes
- [ ] No runtime artifacts committed (`.db`, `.pyc`, `*-wal`, `*-shm`)
- [ ] Backward compatibility considered for public APIs
