"""Package the Windows release zip from this repository.

    python tools/build_release.py

Writes dist/ember-guided-gmod-character-builder-<version>-windows.zip with the same
layout the Builder expects: a single top level folder holding start.bat and the
application beside it.  The archive is deterministic, so rebuilding an unchanged tree
produces an identical file.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"

# Everything the Windows package ships, in the order it is written.
INCLUDE_FILES = (
    "start.bat",
    "verify_v2_guided.bat",
    "check_windows.bat",
    "import_previous_workspace.bat",
    "app.py",
    "self_test.py",
    "pyproject.toml",
    "README.md",
    "READ_ME_FIRST.txt",
    "CHANGELOG.md",
    "VERIFICATION.md",
    "PACKAGE_VERIFICATION.json",
    "LICENSE.txt",
    "workspace/README.txt",
)
INCLUDE_TREES = ("ember_gmod", "blender", "web", "tests")
EXCLUDE_SUFFIXES = (".pyc",)
EXCLUDE_DIRECTORIES = {"__pycache__", ".pytest_cache"}
# Fixed timestamp so an unchanged tree always produces an identical archive.
TIMESTAMP = (2026, 7, 25, 0, 0, 0)


def version() -> str:
    text = (ROOT / "ember_gmod" / "__init__.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("ember_gmod/__init__.py does not define __version__")


def members() -> list[Path]:
    paths: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if not path.is_file():
            raise SystemExit(f"Missing packaged file: {name}")
        paths.append(path)
    for tree in INCLUDE_TREES:
        for path in sorted((ROOT / tree).rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in EXCLUDE_SUFFIXES:
                continue
            if EXCLUDE_DIRECTORIES & set(path.relative_to(ROOT).parts):
                continue
            paths.append(path)
    return paths


def build() -> Path:
    release = version()
    folder = f"ember-guided-gmod-character-builder-v{release}"
    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / f"{folder}-windows.zip"
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in members():
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{folder}/{relative}", date_time=TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
        # The Builder creates project folders here on first run.
        keep = zipfile.ZipInfo(f"{folder}/workspace/projects/.keep", date_time=TIMESTAMP)
        keep.external_attr = 0o644 << 16
        zf.writestr(keep, b"")
    return archive


if __name__ == "__main__":
    output = build()
    print(f"{output.relative_to(ROOT)}  {output.stat().st_size} bytes")
    sys.exit(0)
