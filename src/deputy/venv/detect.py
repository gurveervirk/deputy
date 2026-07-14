import os

_VENV_MARKERS = {"pyvenv.cfg"}

def detect_venv(base_path: str, config: dict[str, str] | None = None) -> str | None:
    if config and config.get("venv_path"):
        candidate = config["venv_path"]
        if os.path.isdir(candidate) and _is_venv(candidate):
            return os.path.abspath(candidate)

    env_venv = os.environ.get("VIRTUAL_ENV")
    if env_venv and os.path.isdir(env_venv) and _is_venv(env_venv):
        return os.path.abspath(env_venv)

    candidate = os.path.join(base_path, ".venv")
    if os.path.isdir(candidate) and _is_venv(candidate):
        return os.path.abspath(candidate)

    return None

def _is_venv(path: str) -> bool:
    return bool(_VENV_MARKERS & set(os.listdir(path)))

def parse_pyvenv_cfg(venv_path: str) -> dict:
    cfg_path = os.path.join(venv_path, "pyvenv.cfg")
    if not os.path.exists(cfg_path):
        return {}
    result = {}
    with open(cfg_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result
