import os
from dataclasses import dataclass, field

@dataclass
class PackageInfo:
    name: str
    version: str
    install_path: str
    top_level_modules: list[str] = field(default_factory=list)
    editable_origin: str | None = None
    mtime: float = 0.0

def find_site_packages(venv_path: str) -> str | None:
    lib_dir = os.path.join(venv_path, "lib")
    if not os.path.isdir(lib_dir):
        return None
    for entry in os.listdir(lib_dir):
        if entry.startswith("python"):
            candidate = os.path.join(lib_dir, entry, "site-packages")
            if os.path.isdir(candidate):
                return os.path.abspath(candidate)
    return None

def list_installed_packages(site_packages_path: str) -> list[PackageInfo]:
    packages: list[PackageInfo] = []
    seen: dict[str, PackageInfo] = {}

    editable_map = _detect_editables(site_packages_path)

    for entry in os.listdir(site_packages_path):
        if entry.endswith(".dist-info"):
            dist_path = os.path.join(site_packages_path, entry)
            name = entry.removesuffix(".dist-info")
            meta = _parse_metadata(os.path.join(dist_path, "METADATA"))
            pkg_name = meta.get("Name", name)
            version = meta.get("Version", "")

            top_level = _parse_top_level(os.path.join(dist_path, "top_level.txt"))

            install_path = site_packages_path
            mtime = _dir_mtime(dist_path)

            existing = seen.get(pkg_name)
            if existing:
                existing.version = version
                existing.top_level_modules = list(set(existing.top_level_modules + top_level))
                existing.mtime = max(existing.mtime, mtime)
            else:
                pkg = PackageInfo(
                    name=pkg_name,
                    version=version,
                    install_path=install_path,
                    top_level_modules=top_level,
                    mtime=mtime,
                )
                if pkg_name in editable_map:
                    pkg.editable_origin = editable_map[pkg_name]
                    pkg.install_path = editable_map[pkg_name]
                packages.append(pkg)
                seen[pkg_name] = pkg

    return packages

def _detect_editables(site_packages_path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in os.listdir(site_packages_path):
        if entry.endswith(".pth"):
            pth_path = os.path.join(site_packages_path, entry)
            with open(pth_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and os.path.isdir(line):
                        result[entry.removesuffix(".pth")] = os.path.abspath(line)
    return result

def _parse_metadata(meta_path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not os.path.exists(meta_path):
        return result
    with open(meta_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                break
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
    return result

def _parse_top_level(top_level_path: str) -> list[str]:
    if not os.path.exists(top_level_path):
        return []
    with open(top_level_path) as f:
        return [line.strip() for line in f if line.strip()]

def _dir_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
