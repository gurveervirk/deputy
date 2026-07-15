import os
import ast
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
            dist_info_key = entry.removesuffix(".dist-info")
            meta = _parse_metadata(os.path.join(dist_path, "METADATA"))
            pkg_name = meta.get("Name", dist_info_key)
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
                if dist_info_key in editable_map:
                    pkg.editable_origin = editable_map[dist_info_key]
                    pkg.install_path = editable_map[dist_info_key]
                packages.append(pkg)
                seen[pkg_name] = pkg

    return packages

def _detect_editables(site_packages_path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in os.listdir(site_packages_path):
        if entry.endswith(".pth"):
            pth_path = os.path.join(site_packages_path, entry)
            with open(pth_path) as f:
                lines = [l.strip() for l in f if l.strip()]
            for line in lines:
                if not line.startswith("#") and os.path.isdir(line):
                    result[entry.removesuffix(".pth")] = os.path.abspath(line)
                    break
            else:
                origin = _resolve_editable_origin(site_packages_path, entry, lines)
                if origin:
                    result[origin[0]] = origin[1]
    return result

def _resolve_editable_origin(
    site_packages_path: str, pth_filename: str, lines: list[str]
) -> tuple[str, str] | None:
    finder_name = _parse_editable_finder_name(lines)
    if not finder_name:
        return None
    mapping = _parse_editable_mapping(site_packages_path, finder_name)
    if not mapping:
        return None
    first_path = next(iter(mapping.values()))
    dist_info_key = pth_filename.removeprefix("__editable__.").removesuffix(".pth")
    return dist_info_key, first_path

def _parse_editable_finder_name(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith("import ") and "finder" in line:
            parts = line.split(";")[0].strip()
            name = parts.removeprefix("import ").strip()
            if name:
                return name
        elif line.startswith("from ") and "finder" in line:
            parts = line.split("import")
            if len(parts) >= 2:
                return parts[-1].strip()
    return None

def _parse_editable_mapping(site_packages_path: str, finder_name: str) -> dict[str, str]:
    finder_path = os.path.join(site_packages_path, finder_name + ".py")
    if not os.path.exists(finder_path):
        return {}
    try:
        with open(finder_path) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = getattr(node, "target", None)
                if target is None:
                    continue
                if isinstance(node, ast.Assign):
                    targets = node.targets
                else:
                    targets = [target]
                for t in targets:
                    if isinstance(t, ast.Name) and t.id == "MAPPING" and isinstance(node.value, ast.Dict):
                        result: dict[str, str] = {}
                        for k, v in zip(node.value.keys, node.value.values):
                            if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                                key_parts = k.value.split(".")
                                root = os.path.abspath(os.path.join(v.value, *[".."] * len(key_parts)))
                                result[k.value] = root
                        return result
    except Exception:
        pass
    return {}

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
