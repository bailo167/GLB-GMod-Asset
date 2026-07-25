from __future__ import annotations

import json
import os
import platform
import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class ToolchainConfig:
    blender: str = ""
    studiomdl: str = ""
    vtex: str = ""
    gmad: str = ""
    game_dir: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> ToolchainConfig:
        detected = detect_toolchain()
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                values: dict[str, str] = {}
                changed = False
                for key in ToolchainConfig.__annotations__:
                    configured = str(raw.get(key, "")).strip()
                    detected_value = str(getattr(detected, key, "")).strip()
                    if configured:
                        values[key] = configured
                    else:
                        values[key] = detected_value
                        changed = changed or bool(detected_value)
                config = ToolchainConfig(**values)
                if changed:
                    self.save(config)
                return config
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        self.save(detected)
        return detected

    def save(self, config: ToolchainConfig) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")


def _first_existing(candidates: list[str | Path]) -> str:
    for candidate in candidates:
        if not candidate:
            continue
        p = Path(candidate).expanduser()
        if p.exists():
            return str(p.resolve())
    return ""


def _windows_steam_install_paths() -> list[Path]:
    paths: list[Path] = []
    defaults = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
    ]
    paths.extend(defaults)
    try:
        import winreg  # type: ignore

        registry_locations = [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
        ]
        for hive, key_name, value_name in registry_locations:
            try:
                with winreg.OpenKey(hive, key_name) as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
                    if value:
                        paths.append(Path(str(value)))
            except OSError:
                continue
    except ImportError:
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _steam_library_paths(steam_root: Path) -> list[Path]:
    libraries = [steam_root]
    vdf = steam_root / "steamapps" / "libraryfolders.vdf"
    if vdf.exists():
        try:
            text = vdf.read_text(encoding="utf-8", errors="replace")
            for raw in re.findall(r'"path"\s+"([^"]+)"', text, flags=re.IGNORECASE):
                libraries.append(Path(raw.replace(r"\\", "\\")))
        except OSError:
            pass
    unique: list[Path] = []
    seen: set[str] = set()
    for path in libraries:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _detect_gmod_root() -> str:
    candidates: list[Path] = []
    for steam_root in _windows_steam_install_paths():
        for library in _steam_library_paths(steam_root):
            candidates.append(library / "steamapps" / "common" / "GarrysMod")
    return _first_existing(candidates)


def _detect_blender() -> str:
    found = shutil.which("blender") or shutil.which("blender.exe")
    if found:
        return str(Path(found).resolve())

    candidates: list[Path] = []
    for base in [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Blender Foundation",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Blender Foundation",
    ]:
        if base.exists():
            candidates.extend(sorted(base.glob("Blender */blender.exe"), reverse=True))
            candidates.append(base / "Blender/blender.exe")

    for steam_root in _windows_steam_install_paths():
        for library in _steam_library_paths(steam_root):
            candidates.append(library / "steamapps/common/Blender/blender.exe")
    return _first_existing(candidates)


def detect_toolchain() -> ToolchainConfig:
    system = platform.system().lower()
    if system != "windows":
        return ToolchainConfig(blender=shutil.which("blender") or "")

    blender = _detect_blender()
    game_root = _detect_gmod_root()
    root = Path(game_root) if game_root else None
    bin_candidates: list[Path] = []
    if root:
        bin_candidates.extend([root / "bin", root / "bin/win64"])

    def locate(names: list[str]) -> str:
        candidates: list[Path] = []
        for directory in bin_candidates:
            candidates.extend(directory / name for name in names)
        for name in names:
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
        return _first_existing(candidates)

    return ToolchainConfig(
        blender=blender,
        studiomdl=locate(["studiomdl.exe"]),
        vtex=locate(["vtex.exe"]),
        gmad=locate(["gmad.exe"]),
        game_dir=str((root / "garrysmod").resolve()) if root and (root / "garrysmod").exists() else "",
    )


def tool_status(config: ToolchainConfig) -> dict[str, Any]:
    system = platform.system()
    out: dict[str, Any] = {
        "platform": system,
        "supported_platform": system.lower() == "windows",
        "python": platform.python_version(),
        "tools": {},
    }
    for key, value in config.to_dict().items():
        if key == "game_dir":
            exists = bool(value and Path(value).is_dir() and (Path(value) / "gameinfo.txt").exists())
        else:
            exists = bool(value and Path(value).is_file())
        out["tools"][key] = {"path": value, "available": exists}
    out["ready_for_blender"] = out["tools"]["blender"]["available"]
    out["ready_for_compile"] = out["tools"]["studiomdl"]["available"] and out["tools"]["game_dir"]["available"]
    out["ready_for_textures"] = True
    out["internal_vtf_writer"] = {"available": True, "version": "VTF 7.2 BGRA8888"}
    out["ready_for_gma"] = out["tools"]["gmad"]["available"]
    return out
