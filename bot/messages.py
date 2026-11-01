"""Loads the [switches] and text templates from config.toml.

([display] and [timing] in the same file are read by config.py.)

Everything is checked at startup, so a typo in the file stops the bot with a
clear error instead of breaking the first death message at 3 am.
"""

from __future__ import annotations

import random
import string
from pathlib import Path

from .config import SETTINGS, ConfigError, read_toml, toml_section

_STATUS = {"server", "state", "players", "max_players", "uptime", "world"}

# Template key -> placeholders it may use.
SCHEMA: dict[str, set[str]] = {
    "status_channel.online": _STATUS,
    "status_channel.starting": _STATUS,
    "status_channel.offline": _STATUS,
    "status_channel.unknown": _STATUS,
    "players_channel.names": {"server", "names", "players", "max_players"},
    "players_channel.count": {"server", "players", "max_players"},
    "players_channel.empty": {"server", "players", "max_players"},
    "presence.online": _STATUS,
    "presence.offline": _STATUS,
    "events.server_online": {"server"},
    "events.server_offline": {"server"},
    "events.player_joined": {"name", "server"},
    "events.player_left": {"name", "server"},
    "events.death": {"name", "server"},
    "events.raid": {"server", "attackers", "raid"},
    "events.raid_unknown": {"server", "raid"},
}

# A switch missing from the file counts as on.
SWITCHES = (
    "category_bar", "status_row", "player_row", "player_names", "presence",
    "server_online", "server_offline", "player_joined", "player_left", "death", "raid",
)

# Text section -> the texts it holds.
TEXT_SECTIONS: dict[str, list[str]] = {}
for _key in SCHEMA:
    _section, _name = _key.split(".")
    TEXT_SECTIONS.setdefault(_section, []).append(_name)

# Every section config.toml may have; [display] and [timing] come from config.py.
SECTIONS = (*SETTINGS, "switches", *TEXT_SECTIONS, "raids")


def _placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in string.Formatter().parse(template) if field is not None}


class Messages:
    def __init__(self, templates: dict[str, list[str]], raids: dict[str, str], switches: dict[str, bool]) -> None:
        self._templates = templates
        self._raids = raids
        self._switches = switches

    @classmethod
    def load(cls, path: Path) -> Messages:
        data = read_toml(path)
        if unknown := set(data) - set(SECTIONS):
            raise ConfigError(
                f"{path}: unknown section(s) {', '.join(f'[{s}]' for s in sorted(unknown))}. "
                f"Known: {', '.join(f'[{s}]' for s in SECTIONS)}"
            )
        for section, names in TEXT_SECTIONS.items():
            if unknown := set(toml_section(data, section, path)) - set(names):
                raise ConfigError(
                    f"{path}: unknown text(s) {', '.join(sorted(unknown))} under [{section}]. Known: {', '.join(names)}"
                )

        switches = dict.fromkeys(SWITCHES, True)
        for name, value in toml_section(data, "switches", path).items():
            if name not in switches:
                raise ConfigError(f"{path}: unknown switch {name!r} under [switches]. Known: {', '.join(SWITCHES)}")
            if not isinstance(value, bool):
                raise ConfigError(f"{path}: switch {name} must be true or false, got {value!r}")
            switches[name] = value

        templates: dict[str, list[str]] = {}
        for key, allowed in SCHEMA.items():
            section, name = key.split(".")
            value = data.get(section, {}).get(name)
            if value is None:
                raise ConfigError(f"{path}: missing {name} = \"...\" under [{section}]")
            if isinstance(value, list) and section != "events":
                raise ConfigError(f"{path}: [{section}] {name} must be a single text (lists only work under [events])")
            options = [value] if isinstance(value, str) else value
            if not isinstance(options, list) or not all(isinstance(o, str) for o in options):
                raise ConfigError(f"{path}: [{section}] {name} must be a text or a list of texts")
            options = [o for o in options if o.strip()]
            if not options:
                raise ConfigError(f"{path}: [{section}] {name} can't be empty (use [switches] to turn it off)")
            for option in options:
                try:
                    unknown = _placeholders(option) - allowed
                except ValueError as exc:  # e.g. a lone "{"
                    raise ConfigError(f"{path}: [{section}] {name}: {exc} in {option!r}") from None
                if unknown:
                    raise ConfigError(
                        f"{path}: [{section}] {name} uses unknown placeholder(s) "
                        f"{', '.join('{' + u + '}' for u in sorted(unknown))}. "
                        f"Allowed: {', '.join('{' + a + '}' for a in sorted(allowed))}"
                    )
            templates[key] = options

        raids = toml_section(data, "raids", path)
        if not all(isinstance(v, str) for v in raids.values()):
            raise ConfigError(f"{path}: every entry under [raids] must be a text")
        return cls(templates, raids, switches)

    def enabled(self, switch: str) -> bool:
        return self._switches[switch]

    def render(self, key: str, **values: object) -> str:
        return random.choice(self._templates[key]).format(**values)

    def raid_attackers(self, raid: str) -> str | None:
        return self._raids.get(raid)
