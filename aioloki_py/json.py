"""Fast JSON serialization powered by orjson.

Thin wrapper that returns ``str`` instead of ``bytes``, suitable
for use as ``json_serialize`` callback in aiohttp.
"""

from typing import Any

import orjson


def dumps(obj: Any, /) -> str:
    """Serialize an object to a JSON string.

    Args:
        obj: The object to serialize.

    Returns:
        A UTF-8 JSON string.
    """
    return orjson.dumps(obj).decode("utf-8")
