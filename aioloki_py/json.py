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


def loads(s: str, /) -> Any:
    """Deserialize a JSON string to a Python object.

    Args:
        s: The JSON string to deserialize.

    Returns:
        The deserialized Python object.
    """
    return orjson.loads(s)
