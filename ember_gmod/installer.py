from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from .runtime_addon import ensure_runtime_addon_files
from .vtf_writer import inspect_vtf


MODEL_EXTENSIONS = (".mdl", ".vvd", ".dx90.vtx", ".phy")
VERSION = "2.3.0"


def _inside(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    candidate = candidate.resolve()
    return candidate == root or root in candidate.parents


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_compiled_model(addon: Path, slug: str, category: str = "player") -> list[str]:
    model_dir = addon / "models" / category / slug
    missing = [str(model_dir / f"{slug}{ext}") for ext in MODEL_EXTENSIONS if not (model_dir / f"{slug}{ext}").is_file()]
    if missing:
        raise RuntimeError("Compiled model output is incomplete: " + ", ".join(missing))
    mdl = model_dir / f"{slug}.mdl"
    if mdl.stat().st_size < 1024:
        raise RuntimeError("Compiled MDL is unexpectedly small and was not installed.")
    magic = mdl.read_bytes()[:4]
    if magic not in {b"IDST", b"IDAG"}:
        raise RuntimeError("Compiled MDL does not have a recognised Source model header.")
    return [str(model_dir / f"{slug}{ext}") for ext in MODEL_EXTENSIONS]


def install_to_gmod(
    addon_source: Path,
    game_dir: Path,
    slug: str,
    display_name: str,
    project_id: str,
    build_token: str = "",
    asset_type: str = "character",
    generate_npcs: bool = False,
) -> dict[str, Any]:
    """Install both as a managed addon and as verified direct game content.

    The direct copy is intentional. It removes reliance on legacy addon mounting for
    local testing while the managed addon remains available for normal packaging.
    A manifest records every direct file so subsequent installs can cleanly replace it.
    """
    addon_source = addon_source.resolve()
    game_dir = game_dir.resolve()
    category = "props" if asset_type == "prop" else "player"
    if not (game_dir / "gameinfo.txt").is_file():
        raise RuntimeError("The selected Garry's Mod game directory does not contain gameinfo.txt.")
    ensure_runtime_addon_files(
        addon_source,
        slug,
        display_name,
        project_id=project_id,
        build_token=build_token,
        asset_type=asset_type,
        generate_npcs=generate_npcs,
    )
    _verify_compiled_model(addon_source, slug, category)

    materials_dir = addon_source / "materials" / "models" / category / slug
    if not materials_dir.is_dir() or not any(materials_dir.glob("*.vmt")) or not any(materials_dir.glob("*.vtf")):
        raise RuntimeError("Source materials are incomplete. At least one VMT and validated VTF are required.")
    invalid_vtf: list[str] = []
    for texture in materials_dir.glob("*.vtf"):
        try:
            inspect_vtf(texture)
        except Exception as exc:
            invalid_vtf.append(f"{texture.name}: {exc}")
    if invalid_vtf:
        raise RuntimeError("These texture files failed full VTF validation: " + "; ".join(sorted(invalid_vtf)))

    addons_dir = game_dir / "addons"
    addons_dir.mkdir(parents=True, exist_ok=True)
    target = addons_dir / f"ember_{slug}"
    marker = target / ".ember_gmod_builder"
    if target.exists() and not marker.exists():
        raise RuntimeError(f"Install target already exists and is not managed by Ember: {target}")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(addon_source, target)
    marker.write_text(json.dumps({"project_id": project_id, "installed": time.time()}, indent=2), encoding="utf-8")

    manifest_dir = game_dir / "data" / "ember_character_builder"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / f"{slug}.json"
    runtime_result_path = manifest_dir / f"runtime_{slug}.json"
    runtime_result_path.write_text(json.dumps({
        "version": VERSION,
        "project_id": project_id,
        "build_token": build_token,
        "status": "installed_waiting_for_game",
        "passed": False,
        "installed": time.time(),
    }, indent=2), encoding="utf-8")
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            for rel in old.get("direct_files", []):
                stale = game_dir / rel
                if _inside(game_dir, stale) and stale.is_file():
                    stale.unlink()
        except (OSError, json.JSONDecodeError, TypeError):
            pass

    # Remove known pre 1.2 runtime file names even if no manifest exists.
    legacy_files = [
        game_dir / "lua" / "autorun" / f"{slug}.lua",
        game_dir / "lua" / "autorun" / "client" / f"{slug}_playermodel.lua",
        game_dir / "lua" / "autorun" / "client" / f"{slug}_validation.lua",
        game_dir / "lua" / "autorun" / "client" / f"ember_{slug}_validation.lua",
    ]
    for stale in legacy_files:
        if _inside(game_dir, stale) and stale.is_file():
            stale.unlink()

    direct_files: list[str] = []
    hashes: dict[str, str] = {}
    for root_name in ("lua", "materials", "models"):
        source_root = addon_source / root_name
        if not source_root.exists():
            continue
        for source in source_root.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(addon_source)
            destination = game_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            rel_text = str(relative).replace("\\", "/")
            direct_files.append(rel_text)
            source_hash = _sha256(source)
            destination_hash = _sha256(destination)
            if source_hash != destination_hash:
                raise RuntimeError(f"Installed file hash mismatch: {rel_text}")
            hashes[rel_text] = destination_hash

    required_runtime = [
        f"lua/autorun/ember_{slug}.lua",
        f"models/{category}/{slug}/{slug}.mdl",
        f"materials/models/{category}/{slug}",
    ]
    if asset_type == "character":
        required_runtime.extend([
            f"lua/autorun/client/00_ember_{slug}_player.lua",
            f"lua/entities/ember_{slug}_ragdoll/shared.lua",
        ])
        if generate_npcs:
            required_runtime.append(f"lua/entities/ember_{slug}_npc_base/shared.lua")
    missing_after_install: list[str] = []
    for relative in required_runtime:
        candidate = game_dir / relative
        if relative.endswith(slug):
            if not candidate.is_dir():
                missing_after_install.append(relative)
        elif not candidate.is_file():
            missing_after_install.append(relative)
    if missing_after_install:
        raise RuntimeError("Post install verification failed: " + ", ".join(missing_after_install))

    registration_text = (game_dir / f"lua/autorun/ember_{slug}.lua").read_text(encoding="utf-8", errors="replace")
    expected_model = f"models/{category}/{slug}/{slug}.mdl"
    if f'local ID = "{slug}"' not in registration_text or expected_model not in registration_text:
        raise RuntimeError("Installed registration does not contain the expected ID and model path.")
    if asset_type == "character":
        client_registration_text = (game_dir / f"lua/autorun/client/00_ember_{slug}_player.lua").read_text(encoding="utf-8", errors="replace")
        required_registration = (
            "player_manager.AddValidModel(ID, MODEL)",
            'list.Set("PlayerOptionsModel", DISPLAY, MODEL)',
            'hook.Add("InitPostEntity"',
            "spawnmenu.AddPropCategory",
            "file.Write(RESULT_FILE",
        )
        required_client_registration = (
            "player_manager.AddValidModel(ID, MODEL)",
            'list.Set("PlayerOptionsModel", ID, MODEL)',
            'list.Set("PlayerOptionsModel", DISPLAY, MODEL)',
            'hook.Add("PopulatePlayerOptions"',
            "timer.Simple(5, register)",
        )
    else:
        client_registration_text = ""
        required_registration = (
            "spawnmenu.AddPropCategory",
            "resource.AddFile(MODEL)",
            "file.Write(RESULT_FILE",
        )
        required_client_registration = ()
    missing_registration = [token for token in required_registration if token not in registration_text]
    if missing_registration:
        raise RuntimeError("Installed runtime registration is incomplete: " + ", ".join(missing_registration))
    if "resource.AddSingleFile" in registration_text:
        raise RuntimeError("Installed registration still contains the removed resource.AddSingleFile call.")
    missing_client = [token for token in required_client_registration if token not in client_registration_text]
    if missing_client:
        raise RuntimeError("Installed dedicated client selector registration is incomplete: " + ", ".join(missing_client))

    if asset_type == "character":
        verification = {
            "shared_player_registration": True,
            "dedicated_client_registration": True,
            "player_options_model": 'list.Set("PlayerOptionsModel", DISPLAY, MODEL)' in registration_text,
            "player_options_model_id": 'list.Set("PlayerOptionsModel", ID, MODEL)' in client_registration_text,
            "registration_retries": 'timer.Simple(5, registerModel)' in registration_text,
            "runtime_result_sentinel": runtime_result_path.is_file(),
            "spawnlist_registration": "spawnmenu.AddPropCategory" in registration_text,
            "spawnable_entity": (game_dir / f"lua/entities/ember_{slug}_ragdoll/shared.lua").is_file(),
            "npc_entities_installed": (game_dir / f"lua/entities/ember_{slug}_npc_base/shared.lua").is_file() if generate_npcs else True,
            "compiled_model": True,
            "materials": True,
            "full_vtf_validation": True,
            "runtime_validation_required": True,
            "all_direct_file_hashes_verified": True,
        }
    else:
        verification = {
            "prop_registration": True,
            "runtime_result_sentinel": runtime_result_path.is_file(),
            "spawnlist_registration": "spawnmenu.AddPropCategory" in registration_text,
            "compiled_model": True,
            "materials": True,
            "full_vtf_validation": True,
            "runtime_validation_required": True,
            "all_direct_file_hashes_verified": True,
        }
    manifest = {
        "version": VERSION,
        "project_id": project_id,
        "build_token": build_token,
        "slug": slug,
        "display_name": display_name,
        "asset_type": asset_type,
        "installed": time.time(),
        "managed_addon": str(target),
        "direct_files": sorted(direct_files),
        "sha256": hashes,
        "verification": verification,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "installed": True,
        "addon_path": str(target),
        "game_dir": str(game_dir),
        "manifest": str(manifest_path),
        "direct_file_count": len(direct_files),
        "verification": manifest["verification"],
        "restart_required": True,
    }
