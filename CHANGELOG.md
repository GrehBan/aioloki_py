# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-02-01

### Added

- Added `CHANGELOG.md` with all notable changes.
- `BaseLokiEmitter.closed` property — returns `True` when the emitter has been
  shut down or its underlying `aiohttp` session is closed.
- `BaseHandler.closed` property — composite check: handler deregistered,
  `logging.Handler.close()` called, and emitter closed.
- `BaseHandler._handler_name`, `_handler_closed`, `_handlers_clean` helper
  properties for safe introspection without `AttributeError`.
- `LoopNotFoundError(RuntimeError)` in `aio` — dedicated exception raised by
  `spawn_task` when no event loop is running (replaces bare `RuntimeError`).
- `ahandleError` async method on `BaseHandler` — closes the handler via
  `aclose` and delegates to `logging.Handler.handleError` for traceback output.
- Emitter lifecycle consolidated into `BaseHandler.__init__`: URL, tags, auth,
  headers, SSL verification, and version selection are now handler-level
  constructor arguments.
- `BaseHandler.emitters` class-level registry mapping version strings to
  emitter classes.
- Deprecation warning in `BaseHandler.__init__` when the default emitter
  version resolves to `"0"` (old `/api/prom/push` endpoint).

### Changed

- `BaseLokiEmitter.__init__` now initialises `_closed: bool = False` eagerly.
- `BaseLokiEmitter.aclose` sets `_closed = True` before draining tasks.
- `BaseLokiEmitter.close` (sync) also sets `_closed = True` before delegating
  to `force_run`.
- `BaseLokiEmitter.session` property raises `RuntimeError` when `_closed` is
  `True`, not only when the session is `None`.
- `BaseLokiEmitter.create_session` resets `_closed = False` after a new
  session is created, enabling handler reuse after `aclose`.
- `spawn_task` uses `asyncio._get_running_loop()` instead of
  `asyncio.get_running_loop()` and raises `LoopNotFoundError` on `None`.
- `force_run` catches `LoopNotFoundError` instead of bare `RuntimeError`.
- `BaseHandler` no longer declares bare `_closed: bool` / `_name: str | None`
  class-level annotations; state is accessed via the new helper properties.
- `BaseHandler.__init__` signature extended — now accepts `url`, `tags`,
  `auth`, `version`, `headers`, and `verify_ssl` and constructs the emitter
  internally.

### Removed

- `json.loads` helper function (unused internally).

### Fixed

- `BaseLokiEmitter.create_session` now resets `_closed = False` after
  creating a new session, preventing a handler from appearing permanently
  closed after its session is recycled (`0.1.1` hotfix).

## [0.1.0] - 2026-02-28

### Added

- Initial public release of **aioloki-py**.
- `BaseLokiEmitter` — abstract base class for Loki HTTP push emitters with
  lazy `aiohttp.ClientSession` management, pending-task draining, and
  both async (`aclose`) and sync (`close`) shutdown.
- `LokiEmitter` (v1/v2) and `LokiEmitterV0` (legacy `/api/prom/push`) concrete
  emitter implementations.
- `LokiHandler` — synchronous-style logging handler that emits each record
  directly to Loki via the configured emitter.
- `LokiQueueHandler` — async queue-based handler that buffers records in an
  `asyncio.Queue` and processes them via a background task for high-throughput
  scenarios.
- `BaseHandler` — shared base class providing `_closed` / `_name` attribute
  initialisation and a `closed` property.
- `spawn_task` / `force_run` helpers in `aio` for scheduling coroutines on the
  running event loop with a synchronous fallback.
- `json.dumps` / `json.loads` wrappers around `orjson` for UTF-8 JSON
  serialisation used in Loki push payloads.
- `constants` module with default emitter version and Loki-compatible label
  sanitisation configuration.
- Full type annotations; `py.typed` marker for PEP 561 compliance.
- `pyproject.toml` with `ruff`, `isort`, and `mypy` toolchain configuration.
- `README.md` with installation, quick-start, and API reference.
- MIT `LICENSE`.

[Unreleased]: https://github.com/GrehBan/aioloki-py/compare/0.1.1...HEAD
[0.1.1]: https://github.com/GrehBan/aioloki-py/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/GrehBan/aioloki-py/releases/tag/0.1.0
