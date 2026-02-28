"""Shared constants for the aioloki-py library.

Defines default emitter version, Loki API expectations, label
tag names, and label sanitization rules.
"""

import string
from typing import Final

EMITTER_VER: Final[str] = "0"
"""Default emitter API version. ``"0"`` targets the legacy
``/api/prom/push`` endpoint."""

FORMAT_LABEL_LRU_SIZE: Final[int] = 256
"""Maximum number of entries in the label formatting LRU cache."""

SUCCESS_RESPONSE_CODE: Final[int] = 204
"""HTTP status code returned by Loki on a successful push."""

LEVEL_TAG: Final[str] = "severity"
"""Label key used to store the log level."""

LOGGER_TAG: Final[str] = "logger"
"""Label key used to store the logger name."""

LABEL_ALLOWED_CHARS: Final[str] = "".join(
    (string.ascii_letters, string.digits, "_")
)
"""Characters permitted in Loki label names after sanitization."""

LABEL_REPLACE_WITH: Final[tuple[tuple[str, str], ...]] = (
    ("'", ""),
    ('"', ""),
    (" ", "_"),
    (".", "_"),
    ("-", "_"),
)
"""Character replacement pairs applied during label sanitization.

Each tuple is ``(source, replacement)``. Applied in order before
filtering by :data:`LABEL_ALLOWED_CHARS`.
"""
