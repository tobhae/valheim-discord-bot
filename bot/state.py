"""Tiny JSON file for things the bot must remember across restarts.

Channel and category IDs, when the server came online (uptime), rename timers
and Discord's channel-creation backoff. Lives in data/state.json by default.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text())
            except (OSError, ValueError):
                log.warning("Could not read %s, starting with empty state", path)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Store a value. Pass a new object, not one from get() changed in place:
        an unchanged value is not written again."""
        if key in self._data and self._data[key] == value:
            return
        self._data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(self._path)
