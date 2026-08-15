import logging
import os
from pathlib import Path

from deputy.utils.config_file import read_config

_log_initialized = False


def _reset_logging() -> None:
    global _log_initialized
    root = logging.getLogger("deputy")
    root.handlers.clear()
    _log_initialized = False


def init_logging(level: str | None = None, log_file: str | None = None) -> None:
    global _log_initialized
    if _log_initialized:
        return

    cfg = read_config()

    if level is None:
        env_level = os.environ.get("DEPUTY_LOG_LEVEL")
        level = env_level or cfg.get("log_level", "WARNING")

    if log_file is None:
        log_file = cfg.get("log_file", ".deputy/deputy.log")

    log_file = os.path.abspath(log_file)

    log_dir = os.path.dirname(log_file)
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("deputy")
    root.setLevel(getattr(logging, level.upper(), logging.WARNING))

    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "[%(asctime)s] %(name)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    _log_initialized = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"deputy.{name}")
