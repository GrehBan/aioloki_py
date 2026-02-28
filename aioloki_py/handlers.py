"""Python logging handlers for Grafana Loki.

Provides two handler classes that integrate with Python's standard
``logging`` module to ship log records to a Loki instance:

- :class:`LokiHandler` -- emits each record directly.
- :class:`LokiQueueHandler` -- buffers records in an asyncio queue
  and sends them via a background task.
"""

import asyncio
import copy
import logging
import warnings
import weakref
from typing import Any, cast

from aioloki_py import aio, constants, emitter

_handlers = cast(
    weakref.WeakValueDictionary,  # type: ignore[type-arg]
    logging._handlers,  # type: ignore[attr-defined]
)


class BaseHandler(logging.Handler):
    """Base handler with explicit ``_closed`` tracking.

    Extends ``logging.Handler`` to initialize ``_closed`` and
    ``_name`` attributes eagerly, avoiding ``AttributeError``
    when accessed before ``close()`` is called.

    Args:
        level: The logging level threshold.
    """

    _closed: bool
    _name: str | None

    def __init__(
        self,
        level: int = logging.NOTSET
    ) -> None:
        super().__init__(level=level)
        self._closed = False
        self._name = None


class LokiQueueHandler(BaseHandler):
    """Async queue-based Loki logging handler.

    Buffers log records in an ``asyncio.Queue`` and processes them
    sequentially via a background task. Preferred for high-throughput
    use to avoid blocking the caller on each HTTP request.

    Args:
        url: The Loki push API endpoint URL.
        level: The logging level threshold.
        queue: Optional pre-existing queue; one is created if
            ``None``.
        tags: Static labels attached to every log entry.
        auth: Optional ``(username, password)`` for HTTP Basic
            Auth.
        version: Emitter API version (``"0"``, ``"1"``, or
            ``"2"``).
        headers: Optional extra HTTP headers.
        verify_ssl: Whether to verify SSL certificates.
    """

    def __init__(
        self,
        url: str,
        level: int = logging.NOTSET,
        queue: asyncio.Queue[logging.LogRecord] | None = None,
        tags: dict[str, str] | None = None,
        auth: emitter.Auth | None = None,
        version: str | None = None,
        headers: dict[str, Any] | None = None,
        verify_ssl: bool = True
    ) -> None:
        super().__init__(level=level)

        self.handler = LokiHandler(
            url=url,
            tags=tags,
            auth=auth,
            version=version,
            headers=headers,
            verify_ssl=verify_ssl
        )

        self._stop_event: asyncio.Event = asyncio.Event()
        self._queue: asyncio.Queue[
            logging.LogRecord
        ] = queue or asyncio.Queue()
        self._task = aio.spawn_task(self._handle_records())

    @property
    def closed(self) -> bool:
        """Whether the handler and its inner handler are fully closed."""
        return (
            self.handler._closed
            and self._closed
            and self._stop_event.is_set()
            and (
                self._name is None
                or self._name not in _handlers
            )
            and (
                self.handler._name is None
                or self.handler._name not in _handlers
            )
        )

    def prepare(
        self, record: logging.LogRecord
    ) -> logging.LogRecord:
        """Format and snapshot a log record for the queue.

        Creates a shallow copy with the formatted message baked in
        and exception/stack info cleared, so it can be safely
        processed later by the background task.

        Args:
            record: The original log record.

        Returns:
            A prepared copy of the log record.
        """
        msg = self.format(record)
        record = copy.copy(record)
        record.message = msg
        record.msg = msg
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return record

    def enqueue(self, record: logging.LogRecord) -> None:
        """Place a prepared record onto the internal queue.

        Args:
            record: The prepared log record.

        Raises:
            asyncio.QueueFull: If the queue is bounded and full.
        """
        self._queue.put_nowait(record)

    def close(self) -> None:
        """Close the handler and cancel the background task.

        Signals the background loop to stop and cancels the task
        to unblock any pending ``queue.get()`` call.
        """
        if not self.closed:
            super().close()
            self._stop_event.set()
            self._task.cancel()

    async def aclose(self) -> None:
        """Async close: stops the handler and drains the emitter.

        Calls :meth:`close` and then awaits the inner handler's
        async cleanup.
        """
        if not self.closed:
            self.close()
            await self.handler.aclose()

    async def _handle_records(self) -> None:
        """Background loop that dequeues and sends records.

        Polls the queue with a 1-second timeout to allow periodic
        stop-event checks. On ``ValueError`` from Loki (e.g. a
        non-204 response) or task cancellation, performs graceful
        shutdown.
        """
        self._stop_event.clear()

        try:
            while not self._stop_event.is_set():
                try:
                    record = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=1.0
                    )
                    try:
                        await self.handler.send_record(record)
                    except ValueError:
                        await self.aclose()
                        break
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            await self.aclose()

    def emit(self, record: logging.LogRecord) -> None:
        """Prepare and enqueue a log record for async sending.

        Args:
            record: The log record to emit.
        """
        try:
            self.enqueue(self.prepare(record))
        except Exception:
            self.handleError(record)


class LokiHandler(BaseHandler):
    """Direct Loki logging handler.

    Emits each log record immediately by scheduling an async HTTP
    request. Selects the emitter implementation based on the Loki
    API ``version``.

    Args:
        url: The Loki push API endpoint URL.
        level: The logging level threshold.
        tags: Static labels attached to every log entry.
        auth: Optional ``(username, password)`` for HTTP Basic
            Auth.
        version: Emitter API version (``"0"``, ``"1"``, or
            ``"2"``). Defaults to :data:`constants.EMITTER_VER`.
        headers: Optional extra HTTP headers.
        verify_ssl: Whether to verify SSL certificates.
    """

    emitters: dict[str, type[emitter.BaseLokiEmitter]] = {
        "0": emitter.LokiEmitterV0,
        "1": emitter.LokiEmitter,
        "2": emitter.LokiEmitter,
    }

    def __init__(
        self,
        url: str,
        level: int = logging.NOTSET,
        tags: dict[str, str] | None = None,
        auth: emitter.Auth | None = None,
        version: str | None = constants.EMITTER_VER,
        headers: dict[str, Any] | None = None,
        verify_ssl: bool = True
    ) -> None:
        super().__init__(level=level)

        if version is None and constants.EMITTER_VER == "0":
            msg = (
                "Loki /api/prom/push endpoint is in the "
                "depreciation process starting from version "
                "0.4.0. Explicitly set the emitter version "
                "to '0' if you want to use the old endpoint. "
                "Or specify '1' if you have Loki "
                "version >= 0.4.0. When the old API is "
                "removed from Loki, the handler will use "
                "the new version by default."
            )
            warnings.warn(msg, DeprecationWarning)

        self.version = version or constants.EMITTER_VER

        if self.version not in self.emitters:
            raise ValueError(
                f"Unknown emitter version: {version}"
            )

        self.emitter = self.emitters[self.version](
            url=url,
            tags=tags,
            auth=auth,
            headers=headers,
            verify_ssl=verify_ssl
        )

    def close(self) -> None:
        """Close the handler and its underlying emitter."""
        super().close()
        self.emitter.close()

    async def aclose(self) -> None:
        """Async close: the handler and its underlying emitter."""
        super().close()
        await self.emitter.aclose()

    async def send_record(
        self, record: logging.LogRecord
    ) -> None:
        """Format and send a log record to Loki.

        Args:
            record: The log record to send.
        """
        try:
            await self.emitter.send_record(
                record, self.format(record)
            )
        except Exception:
            await self.ahandleError(record)

    async def ahandleError(
        self, record: logging.LogRecord
    ) -> None:
        """Handle an error asynchronously.

        Closes the handler and delegates to the standard
        ``logging.Handler.handleError`` method.

        Args:
            record: The log record that caused the error.
        """
        await self.aclose()
        super().handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        """Handle an error synchronously.

        Closes the handler and delegates to the standard
        ``logging.Handler.handleError`` method.

        Args:
            record: The log record that caused the error.
        """
        self.close()
        super().handleError(record)

    def emit(self, record: logging.LogRecord) -> None:
        """Format and schedule a log record for async sending.

        Args:
            record: The log record to emit.
        """
        try:
            self.emitter(record, self.format(record))
        except Exception:
            self.handleError(record)
