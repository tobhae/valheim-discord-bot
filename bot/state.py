"""Tiny JSON file for things the bot must remember across restarts.

Channel and category IDs, when the server came online (uptime), rename timers
and Discord's channel-creation backoff. Lives in data/state.json by default.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import ConfigError

log = logging.getLogger(__name__)


class State:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text())
                if not isinstance(data, dict):
                    raise ValueError("not a JSON object")
                self._data = data
            except OSError as exc:
                raise ConfigError(
                    f"Can't read {path} ({exc}). It must be readable by the bot (uid 1000 in Docker).",
                ) from None
            except ValueError:
                log.warning("Could not read %s, starting with empty state", path)
        # Without saved channel IDs the bot creates new status rows after every restart, so stop now.
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.touch()
            tmp.unlink()
        except OSError as exc:
            raise ConfigError(
                f"Can't write to {path.parent} ({exc}). The folder must be writable by the bot (uid 1000 in Docker).",
            ) from None

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
