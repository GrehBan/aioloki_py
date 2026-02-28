"""Loki HTTP push emitters.

Provides the base emitter class and two concrete implementations
for different Loki API versions. All HTTP I/O is async via aiohttp;
the ``ClientSession`` is lazily created and reused across log records.
"""

import abc
import asyncio
import functools
import logging
import warnings
import weakref
from typing import Any, TypeAlias

import rfc3339  # type: ignore[import-untyped]
from aiohttp import BasicAuth, ClientSession, TCPConnector

from aioloki_py import aio, constants, json

Auth: TypeAlias = tuple[str, str]  # noqa: UP040
"""Authentication credentials as ``(username, password)``."""


@functools.lru_cache(constants.FORMAT_LABEL_LRU_SIZE)
def _format_label(
    label: str,
    label_replace_with: tuple[tuple[str, str], ...],
    label_allowed_chars: str,
) -> str:
    """Sanitize a label name for Loki compatibility.

    Applies character replacements and filters out disallowed
    characters. Results are cached via LRU cache.

    Args:
        label: The raw label name.
        label_replace_with: Ordered replacement pairs.
        label_allowed_chars: String of permitted characters.

    Returns:
        The sanitized label name.
    """
    for char_from, char_to in label_replace_with:
        label = label.replace(char_from, char_to)
    return "".join(char for char in label if char in label_allowed_chars)


class BaseLokiEmitter(abc.ABC):
    """Abstract base class for Loki log emitters.

    Manages the aiohttp ``ClientSession`` lifecycle, authentication,
    label sanitization, and tag building. Subclasses must implement
    :meth:`build_payload` for their target Loki API version.

    Args:
        url: The Loki push API endpoint URL.
        tags: Static labels attached to every log entry.
        auth: Optional ``(username, password)`` for HTTP Basic Auth.
        headers: Optional extra HTTP headers for every request.
        verify_ssl: Whether to verify SSL certificates.
    """

    success_response_code = constants.SUCCESS_RESPONSE_CODE
    level_tag = constants.LEVEL_TAG
    logger_tag = constants.LOGGER_TAG
    label_allowed_chars = constants.LABEL_ALLOWED_CHARS
    label_replace_with = constants.LABEL_REPLACE_WITH

    def __init__(
        self,
        url: str,
        tags: dict[str, str] | None = None,
        auth: Auth | None = None,
        headers: dict[str, Any] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        self.url = url
        self.tags = tags or {}
        self.auth = auth
        self.headers = headers
        self.verify_ssl = verify_ssl
        self._closed: bool = False
        self._session: ClientSession | None = None
        self._pending_tasks: weakref.WeakSet[asyncio.Task[Any]] = (
            weakref.WeakSet()
        )

    @property
    def closed(self) -> bool:
        """Whether the emitter has been closed or its session has closed."""
        if self._closed:
            return True
        return self._session is not None and self._session.closed

    @property
    def session(self) -> ClientSession:
        """Return the active ``ClientSession``.

        Returns:
            The active aiohttp ``ClientSession``.

        Raises:
            RuntimeError: If the emitter is closed or not yet initialized.
        """
        if self._closed or self._session is None:
            raise RuntimeError("Session is not initialized")
        return self._session

    async def aclose(self) -> None:
        """Drain pending tasks and close the HTTP session.

        Waits up to 1 second for in-flight requests to complete,
        then closes the aiohttp ``ClientSession`` and clears the
        session reference so :attr:`closed` returns ``True``.
        """
        self._closed = True
        await self._drain()
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def close(self) -> None:
        """Close the emitter synchronously.

        Schedules :meth:`aclose` on the running event loop, or
        falls back to ``asyncio.run()`` if no loop is active.
        """
        self._closed = True
        aio.force_run(self.aclose())

    async def create_session(self, close: bool = False) -> ClientSession:
        """Return the ``ClientSession``, creating it if needed.

        The session is lazily initialized on first call and reused
        for subsequent requests.

        Args:
            close: If ``True``, close the existing session first.

        Returns:
            The active aiohttp ``ClientSession``.
        """
        if close:
            await self.aclose()

        if self._session is None or self._session.closed:
            auth = (
                BasicAuth(login=self.auth[0], password=self.auth[1])
                if self.auth
                else None
            )
            self._session = ClientSession(
                auth=auth,
                connector=TCPConnector(ssl=self.verify_ssl),
                headers=self.headers,
                json_serialize=json.dumps,
            )
        return self.session

    @abc.abstractmethod
    def build_payload(
        self, record: logging.LogRecord, line: str
    ) -> dict[str, Any]:
        """Build the JSON payload for a Loki push request.

        Args:
            record: The log record being emitted.
            line: The formatted log message string.

        Returns:
            A dict suitable for JSON serialization as the
            request body.
        """
        raise NotImplementedError

    def __call__(self, record: logging.LogRecord, line: str) -> None:
        """Fire-and-forget: schedule a log record send as a task.

        The task reference is kept in a ``WeakSet`` to prevent
        garbage collection before completion.

        Args:
            record: The log record to send.
            line: The formatted log message string.
        """
        task = aio.spawn_task(self.send_record(record, line))
        self._pending_tasks.add(task)

    async def _drain(self) -> None:
        """Await all pending send tasks with a timeout.

        Waits up to 1 second for in-flight requests to complete.
        Exceptions from individual tasks are suppressed.
        """
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    *self._pending_tasks,
                    return_exceptions=True,
                ),
                timeout=1.0,
            )
        except TimeoutError:
            return

    async def send_record(self, record: logging.LogRecord, line: str) -> None:
        """Send a single log record to Loki.

        Args:
            record: The log record to send.
            line: The formatted log message string.

        Raises:
            ValueError: If Loki responds with a non-204 status.
        """
        session = await self.create_session()

        payload = self.build_payload(record=record, line=line)

        async with session.post(url=self.url, json=payload) as response:
            if response.status != self.success_response_code:
                raise ValueError(
                    f"Unexpected Loki API "
                    f"response status code: {response.status}"
                )

    def format_label(self, label: str) -> str:
        """Sanitize a label name using the class-level rules.

        Args:
            label: The raw label name.

        Returns:
            The sanitized label name.
        """
        return _format_label(
            label, self.label_replace_with, self.label_allowed_chars
        )

    def build_tags(self, record: logging.LogRecord) -> dict[str, str]:
        """Build the full label set for a log record.

        Merges static tags, log level, logger name, and any extra
        tags attached to the record via a ``tags`` attribute.

        Args:
            record: The log record.

        Returns:
            A dict of label name to label value.
        """
        tags = dict(self.tags)
        tags[self.level_tag] = record.levelname.lower()
        tags[self.logger_tag] = record.name

        extra_tags = getattr(record, "tags", {})
        if not isinstance(extra_tags, dict):
            warnings.warn("Extra tags attribute must be a dict", UserWarning)
            return tags

        for tag_name, tag_value in extra_tags.items():
            cleared_name = self.format_label(tag_name)
            if cleared_name:
                tags[cleared_name] = str(tag_value)

        return tags


class LokiEmitterV0(BaseLokiEmitter):
    """Emitter for the legacy ``/api/prom/push`` Loki endpoint.

    Builds payloads with Prometheus-style label strings and
    RFC 3339 microsecond timestamps.
    """

    def build_payload(
        self, record: logging.LogRecord, line: str
    ) -> dict[str, Any]:
        """Build a legacy-format Loki push payload.

        Args:
            record: The log record being emitted.
            line: The formatted log message string.

        Returns:
            A dict with ``streams`` containing Prometheus-style
            labels and an ``entries`` list.
        """
        labels = self.build_labels(record)
        ts = rfc3339.format_microsecond(record.created)
        stream = {
            "labels": labels,
            "entries": [{"ts": ts, "line": line}],
        }
        return {"streams": [stream]}

    def build_labels(self, record: logging.LogRecord) -> str:
        """Build a Prometheus-style labels string.

        Escapes double quotes in values and formats as
        ``{key="value",...}``.

        Args:
            record: The log record.

        Returns:
            A Prometheus-format labels string.
        """
        labels: list[str] = []
        for label_name, label_value in self.build_tags(record).items():
            label_value = label_value.replace('"', r"\"")
            labels.append(f'{label_name}="{label_value}"')
        return "{{{}}}".format(",".join(labels))


class LokiEmitter(BaseLokiEmitter):
    """Emitter for the current Loki push API.

    Builds payloads with JSON dict labels and nanosecond-precision
    timestamps.
    """

    def build_payload(
        self, record: logging.LogRecord, line: str
    ) -> dict[str, Any]:
        """Build a current-format Loki push payload.

        Args:
            record: The log record being emitted.
            line: The formatted log message string.

        Returns:
            A dict with ``streams`` containing a ``stream``
            labels dict and a ``values`` list of
            ``[timestamp, line]`` pairs.
        """
        labels = self.build_tags(record)
        ts = str(round(record.created * 10**9))
        stream = {
            "stream": labels,
            "values": [[ts, line]],
        }
        return {"streams": [stream]}
