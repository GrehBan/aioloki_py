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
    """Base Loki logging handler with emitter lifecycle management.

    Owns the :class:`~emitter.BaseLokiEmitter` instance and provides
    shared infrastructure: emitter selection and initialization,
    ``closed`` state tracking, synchronous and async close, and error
    handling. Subclasses implement :meth:`emit` to define how records
    are dispatched to the emitter.

    Args:
        url: The Loki push API endpoint URL.
        level: The logging level threshold.
        tags: Static labels attached to every log entry.
        auth: Optional ``(username, password)`` for HTTP Basic Auth.
        version: Emitter API version (``"0"``, ``"1"``, or ``"2"``).
            Defaults to ``None``.
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
        version: str | None = None,
        headers: dict[str, Any] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        super().__init__(level=level)

        if version is None and constants.EMITTER_VER == "0":
            msg = (
                "Loki /api/prom/push endpoint is in the "
                "deprecation process starting from version "
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
            raise ValueError(f"Unknown emitter version: {version}")

        self.emitter = self.emitters[self.version](
            url=url,
            tags=tags,
            auth=auth,
            headers=headers,
            verify_ssl=verify_ssl,
        )

    @property
    def _handler_name(self) -> str | None:
        """The handler's registered name, or ``None`` if unnamed."""
        return getattr(self, "_name", None)

    @property
    def _handler_closed(self) -> bool:
        """Whether ``logging.Handler.close()`` has been called."""
        return getattr(self, "_closed", False)

    @property
    def _handlers_clean(self) -> bool:
        """Whether this handler is absent from the global handler registry."""
        return (
            self._handler_name is None or self._handler_name not in _handlers
        )

    @property
    def closed(self) -> bool:
        """Whether the handler has been closed,
        deregistered, and its emitter shut down."""
        return (
            self._handler_closed
            and self._handlers_clean
            and self.emitter.closed
        )

    async def ahandleError(self, record: logging.LogRecord) -> None:
        """Handle a send error asynchronously.

        Closes the handler via :meth:`aclose` and delegates to
        ``logging.Handler.handleError`` to print the traceback.

        Args:
            record: The log record that caused the error.
        """
        await self.aclose()
        super().handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:
        """Handle a send error synchronously.

        Closes the handler via :meth:`close` and delegates to
        ``logging.Handler.handleError`` to print the traceback.

        Args:
            record: The log record that caused the error.
        """
        if not self.closed:
            self.close()
            super().handleError(record)

    def close(self) -> None:
        """Close the handler and its underlying emitter.

        Calls ``logging.Handler.close()`` to deregister the handler,
        then synchronously closes the emitter's HTTP session.
        """
        if not self.closed:
            super().close()
            self.emitter.close()

    async def aclose(self) -> None:
        """Async close: deregister the handler and drain the emitter.

        Calls ``logging.Handler.close()`` to deregister the handler,
        then awaits the emitter's async cleanup, draining any
        in-flight HTTP requests before closing the session.
        """
        if not self.closed:
            super().close()
            await self.emitter.aclose()


class LokiQueueHandler(BaseHandler):
    """Async queue-based Loki logging handler.

    Buffers log records in an ``asyncio.Queue`` and processes them
    sequentially via a background task. Preferred for high-throughput
    use to avoid blocking the caller on each HTTP request.

    .. note::
        Must be instantiated inside a running asyncio event loop.
        A ``RuntimeError`` is raised at construction time if no loop
        is active, because the background processing task is started
        immediately in ``__init__``.

    Args:
        url: The Loki push API endpoint URL.
        level: The logging level threshold.
        queue: Optional pre-existing queue; one is created if
            ``None``.
        tags: Static labels attached to every log entry.
        auth: Optional ``(username, password)`` for HTTP Basic
            Auth.
        version: Emitter API version (``"0"``, ``"1"``, or
            ``"2"``). Defaults to ``None``.
        headers: Optional extra HTTP headers.
        verify_ssl: Whether to verify SSL certificates.
        max_retries: Maximum number of retry attempts per record
            on a non-204 Loki response. ``0`` disables retries.
            Defaults to ``3``.
        retry_delay: Base delay in seconds between retry attempts.
            Actual wait time grows as ``retry_delay * 2 ** n``
            (exponential back-off). Defaults to ``1.0``.
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
        verify_ssl: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> None:
        super().__init__(
            url=url,
            level=level,
            tags=tags,
            auth=auth,
            version=version,
            headers=headers,
            verify_ssl=verify_ssl,
        )

        self._max_retries = max_retries
        self._retry_delay = retry_delay

        self._stop_event: asyncio.Event = asyncio.Event()
        self._queue: asyncio.Queue[logging.LogRecord] = (
            queue or asyncio.Queue()
        )
        self._task = aio.spawn_task(self._handle_records())

    @property
    def _internal_closed(self) -> bool:
        """Whether the stop event is set
        and the background task has finished."""
        return self._stop_event.is_set() and self._task.done()

    @property
    def closed(self) -> bool:
        """Whether the handler and background task are fully closed."""
        return super().closed and self._internal_closed

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
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

    def _internal_close(self) -> None:
        """Mark the handler as closed and cancel the background task.

        Sets the stop event and cancels the background task if it has
        not yet finished.
        """
        self._stop_event.set()
        if not self._task.done():
            self._task.cancel()

    def close(self) -> None:
        """Close the handler, background task, and emitter.

        Calls :meth:`_internal_close` to stop the background loop,
        then delegates to :meth:`BaseHandler.close` for synchronous
        emitter cleanup.
        """
        if not self.closed:
            self._internal_close()
            super().close()

    async def aclose(self) -> None:
        """Async close: stop the background task and drain the emitter.

        Calls :meth:`_internal_close` to stop the background loop,
        then delegates to :meth:`BaseHandler.aclose` for async cleanup,
        draining any in-flight HTTP requests before closing the session.
        """
        if not self.closed:
            self._internal_close()
            await super().aclose()

    async def _send_with_retry(self, record: logging.LogRecord) -> None:
        """Send a record to Loki, retrying on non-204 responses.

        Attempts up to ``_max_retries + 1`` times total. Between
        attempts, waits ``_retry_delay * 2 ** n`` seconds
        (exponential back-off, starting from ``n = 0``).

        Args:
            record: The prepared log record to send.

        Raises:
            ValueError: If all attempts are exhausted with a
                non-204 response from Loki.
        """
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                await asyncio.sleep(self._retry_delay * (2 ** (attempt - 1)))
            try:
                await self.emitter.send_record(record, self.format(record))
                return
            except ValueError:
                if attempt == self._max_retries:
                    raise

    async def _handle_records(self) -> None:
        """Background loop that dequeues and sends records.

        Polls the queue with a 1-second timeout to allow periodic
        stop-event checks. On ``ValueError`` from Loki after all
        retry attempts are exhausted, or on task cancellation,
        performs graceful shutdown.
        """
        self._stop_event.clear()

        try:
            while not self._stop_event.is_set():
                try:
                    record = await asyncio.wait_for(
                        self._queue.get(), timeout=1.0
                    )
                    try:
                        await self._send_with_retry(record)
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

    Emits each log record immediately by scheduling a fire-and-forget
    async HTTP request via the underlying emitter. All emitter
    lifecycle, session management, and error handling are inherited
    from :class:`BaseHandler`.

    See :class:`BaseHandler` for constructor parameters.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Format and schedule a log record for async sending.

        Schedules :meth:`BaseLokiEmitter.__call__` as an asyncio task.
        On any exception (e.g. no running event loop), delegates to
        :meth:`handleError`.

        Args:
            record: The log record to emit.
        """
        try:
            self.emitter(record, self.format(record))
        except Exception:
            self.handleError(record)
