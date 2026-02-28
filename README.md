# aioloki-py

Async Python logging handler for [Grafana Loki](https://grafana.com/oss/loki/). Ships log records to Loki's HTTP push API using `aiohttp`, integrating seamlessly with Python's standard `logging` module.

## Features

- **Fully async** -- all HTTP I/O via `aiohttp` with lazy session management
- **Two handler modes** -- direct (`LokiHandler`) and queue-based (`LokiQueueHandler`) for high-throughput use
- **Retry with back-off** -- `LokiQueueHandler` retries failed pushes with exponential back-off
- **Multiple API versions** -- supports both the legacy `/api/prom/push` (v0) and the current push API (v1/v2)
- **Automatic label sanitization** -- invalid characters are replaced or stripped per Loki's label naming rules
- **Fast JSON** -- uses `orjson` for serialization
- **Typed** -- ships with `py.typed` marker and full type annotations

## Requirements

- Python >= 3.13
- A running Grafana Loki instance

## Installation

```bash
pip install aioloki-py
```

Or with [uv](https://docs.astral.sh/uv/):

```bash
uv add aioloki-py
```

## Quick Start

### Basic usage with `LokiHandler`

```python
import asyncio
import logging

from aioloki_py.handlers import LokiHandler

async def main():
    handler = LokiHandler(
        url="http://localhost:3100/loki/api/v1/push",
        tags={"application": "my-service"},
        version="1",
    )

    logger = logging.getLogger("my-service")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("Hello from aioloki-py!")

    # Clean shutdown
    await handler.aclose()

asyncio.run(main())
```

### Queue-based handler (recommended for production)

`LokiQueueHandler` decouples log production from HTTP transmission via an internal `asyncio.Queue` and a background task.

> **Note:** `LokiQueueHandler` must be instantiated inside a running asyncio event loop. Creating it at module level or before `asyncio.run()` will raise `RuntimeError`.

```python
import asyncio
import logging

from aioloki_py.handlers import LokiQueueHandler

async def main():
    handler = LokiQueueHandler(
        url="http://localhost:3100/loki/api/v1/push",
        tags={"application": "my-service", "environment": "prod"},
        version="1",
    )

    logger = logging.getLogger("my-service")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)

    logger.info("Queued log entry")
    logger.warning("This won't block the caller")

    # Clean shutdown: stops the background task and drains the session
    await handler.aclose()

asyncio.run(main())
```

### With authentication

```python
handler = LokiHandler(
    url="https://loki.example.com/loki/api/v1/push",
    tags={"service": "api"},
    auth=("username", "password"),
    version="1",
)
```

### With custom headers

```python
handler = LokiHandler(
    url="https://loki.example.com/loki/api/v1/push",
    tags={"service": "api"},
    headers={"X-Scope-OrgID": "tenant-1"},
    version="1",
)
```

### Extra tags per log record

Attach additional labels to individual log records via the `tags` extra:

```python
logger.info(
    "User signed in",
    extra={"tags": {"user_id": "abc123", "region": "eu-west-1"}}
)
```

## API Versions

| Version | Endpoint | Format | Timestamps |
|---------|----------|--------|------------|
| `"0"` | `/api/prom/push` (legacy) | Prometheus-style label strings | RFC 3339 microsecond |
| `"1"` / `"2"` | `/loki/api/v1/push` | JSON dict labels | Nanosecond Unix epoch |

> **Note:** Version `"0"` is deprecated and triggers a `DeprecationWarning`. Use `"1"` or `"2"` for Loki >= 0.4.0.

## Retry Behaviour

`LokiQueueHandler` automatically retries failed pushes (non-204 responses) using exponential back-off. Two parameters control this:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_retries` | `3` | Maximum retry attempts per record. `0` disables retries. |
| `retry_delay` | `1.0` | Base delay in seconds. Actual wait: `retry_delay * 2 ** n` for attempt `n`. |

With defaults, a failing record is retried after 1 s, 2 s, and 4 s before the handler closes. To disable retries entirely:

```python
handler = LokiQueueHandler(
    url="http://localhost:3100/loki/api/v1/push",
    version="1",
    max_retries=0,
)
```

## Configuration via `logging.config`

```python
import logging.config

LOGGING_CONFIG = {
    "version": 1,
    "handlers": {
        "loki": {
            "class": "aioloki_py.handlers.LokiQueueHandler",
            "url": "http://localhost:3100/loki/api/v1/push",
            "tags": {"application": "my-service"},
            "version": "1",
            "max_retries": 3,
            "retry_delay": 1.0,
        },
    },
    "root": {
        "handlers": ["loki"],
        "level": "INFO",
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
```

> **Note:** When using `dictConfig`, the handler is instantiated synchronously during `dictConfig()` call. Ensure an event loop is running at that point (e.g. call `dictConfig` inside an `async` function).

## Architecture

```
aioloki_py/
├── aio.py          # asyncio task scheduling helpers
├── constants.py    # Shared constants and label rules
├── json.py         # orjson wrapper (str output)
├── emitter.py      # HTTP push emitters (BaseLokiEmitter, LokiEmitterV0, LokiEmitter)
└── handlers.py     # logging.Handler subclasses (BaseHandler, LokiHandler, LokiQueueHandler)
```

**Emitters** (`emitter.py`) handle the HTTP transport layer: session lifecycle, authentication, label sanitization, and payload construction. Two implementations target different Loki API versions.

**Handlers** (`handlers.py`) bridge Python's `logging` module with the emitters. `BaseHandler` owns the emitter instance and provides shared infrastructure (version selection, close/aclose, error handling). Both concrete handlers extend it directly:

- `LokiHandler` — schedules a fire-and-forget async task per record.
- `LokiQueueHandler` — buffers records in an `asyncio.Queue` and processes them sequentially in a background task with configurable retry.

## Development

```bash
# Install all dependencies including lint tools
uv sync --group lint

# Run the linter
uv run ruff check aioloki_py/

# Run the type checker
uv run mypy aioloki_py/

# Format code
uv run ruff format aioloki_py/
```

## License

See [LICENSE](LICENSE) for details.
