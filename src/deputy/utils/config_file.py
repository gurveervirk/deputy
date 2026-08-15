import os
from pathlib import Path

_CONFIG_FILE = ".deputyconfig"


def read_config() -> dict[str, str]:
    if not os.path.exists(_CONFIG_FILE):
        return {}
    raw = Path(_CONFIG_FILE).read_text().strip()
    if not raw:
        return {}
    lines = raw.splitlines()
    if len(lines) == 1 and "=" not in lines[0]:
        return {"db_path": lines[0]}
    result: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def write_config(key: str, value: str) -> None:
    config = read_config()
    config[key] = value
    lines = [f"{k}={v}" for k, v in config.items()]
    Path(_CONFIG_FILE).write_text("\n".join(lines) + "\n")


def get_config(key: str, default: str | None = None) -> str | None:
    return read_config().get(key, default)
