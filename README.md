# aioloki-py

Async Python logging handler for [Grafana Loki](https://grafana.com/oss/loki/). Ships log records to Loki's HTTP push API using `aiohttp`, integrating seamlessly with Python's standard `logging` module.

## Features

- **Fully async** -- all HTTP I/O via `aiohttp` with lazy session management
- **Two handler modes** -- direct (`LokiHandler`) and queue-based (`LokiQueueHandler`) for high-throughput use
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

`LokiQueueHandler` decouples log production from HTTP transmission via an internal `asyncio.Queue` and a background task:

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

    # Clean shutdown: drains the queue and closes the session
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
        },
    },
    "root": {
        "handlers": ["loki"],
        "level": "INFO",
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
```

## Architecture

```
aioloki_py/
├── aio.py          # asyncio task scheduling helpers
├── constants.py    # Shared constants and label rules
├── json.py         # orjson wrapper (str output)
├── emitter.py      # HTTP push emitters (BaseLokiEmitter, LokiEmitterV0, LokiEmitter)
└── handlers.py     # logging.Handler subclasses (LokiHandler, LokiQueueHandler)
```

**Emitters** (`emitter.py`) handle the HTTP transport layer: session lifecycle, authentication, label sanitization, and payload construction. Two implementations target different Loki API versions.

**Handlers** (`handlers.py`) bridge Python's `logging` module with the emitters. `LokiHandler` schedules a send task per record; `LokiQueueHandler` buffers records and processes them sequentially in a background task.

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
