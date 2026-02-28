"""Asyncio task scheduling utilities.

Helpers for creating asyncio tasks from coroutines, with a
fallback for contexts where no event loop is running.
"""

import asyncio
from collections.abc import Coroutine
from typing import Any


def spawn_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """Schedule a coroutine as an asyncio task on the running loop.

    Args:
        coro: The coroutine to schedule.

    Returns:
        The created asyncio task.

    Raises:
        RuntimeError: If no event loop is currently running.
    """
    return asyncio.get_running_loop().create_task(coro)


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
    except RuntimeError:
        asyncio.run(coro)

    return None
