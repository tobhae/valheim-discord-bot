import sys

from .config import ConfigError
from .main import run

try:
    run()
except ConfigError as exc:
    sys.exit(f"Configuration error: {exc}")
