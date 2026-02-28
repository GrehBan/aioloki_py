"""Asyncio task scheduling utilities.

Helpers for creating asyncio tasks from coroutines, with a
fallback for contexts where no event loop is running.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


class LoopNotFoundError(RuntimeError):
    """Raised by :func:`spawn_task` when no event loop is running."""


def spawn_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule a coroutine as an asyncio task on the running loop.

    Args:
        coro: The coroutine to schedule.

    Returns:
        The created asyncio task.

    Raises:
        LoopNotFoundError: If no event loop is currently running.
    """
    loop = asyncio._get_running_loop()
    if loop is None:
        raise LoopNotFoundError("There is no current event loop")
    return loop.create_task(coro)


def force_run(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any] | None:
    """Schedule a coroutine, falling back to synchronous execution.

    Attempts to schedule the coroutine on the running event loop.
    If no loop is running, executes the coroutine synchronously
    via ``asyncio.run()``.

    Args:
        coro: The coroutine to run.

    Returns:
        The created task if a running loop exists, or ``None``
        if the coroutine was executed synchronously.
    """
    try:
        return spawn_task(coro)
    except LoopNotFoundError:
        asyncio.run(coro)

    return None
