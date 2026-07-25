from __future__ import annotations

import json
from pathlib import Path

from ember_gmod.installer import install_to_gmod
from ember_gmod.vtf_writer import write_vtf_from_tga
from blender.tga_writer import write_uncompressed_tga


def make_compiled_addon(root: Path, slug: str) -> Path:
    addon = root / "addon"
    model_dir = addon / "models" / "player" / slug
    material_dir = addon / "materials" / "models" / "player" / slug
    model_dir.mkdir(parents=True)
    material_dir.mkdir(parents=True)
    (model_dir / f"{slug}.mdl").write_bytes(b"IDST" + b"\0" * 2048)
    (model_dir / f"{slug}.vvd").write_bytes(b"VVD" + b"\0" * 128)
    (model_dir / f"{slug}.dx90.vtx").write_bytes(b"VTX" + b"\0" * 128)
    (model_dir / f"{slug}.phy").write_bytes(b"PHY" + b"\0" * 128)
    (material_dir / "body.vmt").write_text('"VertexLitGeneric" {}\n', encoding="utf-8")
    tga = root / "body.tga"
    write_uncompressed_tga(tga, 4, 4, [0.5, 0.25, 0.75, 1.0] * 16, srgb=True, include_alpha=False)
    write_vtf_from_tga(tga, material_dir / "body.vtf")
    return addon


def test_installer_creates_managed_addon_and_verified_direct_install(tmp_path: Path):
    slug = "test_character"
    addon = make_compiled_addon(tmp_path / "project", slug)
    game = tmp_path / "garrysmod"
    game.mkdir()
    (game / "gameinfo.txt").write_text("GameInfo {}", encoding="utf-8")

    result = install_to_gmod(addon, game, slug, "Test Character", "abc123def456", "token123")

    assert result["installed"] is True
    assert result["restart_required"] is True
    assert result["direct_file_count"] >= 10
    assert all(result["verification"].values())
    assert (game / "addons" / f"ember_{slug}" / ".ember_gmod_builder").exists()
    assert (game / f"lua/autorun/ember_{slug}.lua").exists()
    assert (game / f"lua/entities/ember_{slug}_ragdoll/shared.lua").exists()
    assert (game / f"models/player/{slug}/{slug}.mdl").read_bytes()[:4] == b"IDST"
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["slug"] == slug
    assert manifest["build_token"] == "token123"
    assert manifest["verification"]["spawnable_entity"] is True

    # A second install is a clean managed replacement, not a nested addon.
    result2 = install_to_gmod(addon, game, slug, "Test Character", "abc123def456", "token123")
    assert Path(result2["addon_path"]).name == f"ember_{slug}"
    assert not (Path(result2["addon_path"]) / "addon").exists()


def test_installer_refuses_unmanaged_existing_target(tmp_path: Path):
    slug = "test_character"
    addon = make_compiled_addon(tmp_path / "project", slug)
    game = tmp_path / "garrysmod"
    (game / "addons" / f"ember_{slug}").mkdir(parents=True)
    (game / "gameinfo.txt").write_text("GameInfo {}", encoding="utf-8")
    try:
        install_to_gmod(addon, game, slug, "Test Character", "abc123def456", "token123")
    except RuntimeError as exc:
        assert "not managed by Ember" in str(exc)
    else:
        raise AssertionError("Unmanaged addon target was overwritten")


def test_installer_manifest_hashes_cover_dedicated_selector_and_model(tmp_path: Path):
    import hashlib
    slug = "selector_hash_test"
    addon = make_compiled_addon(tmp_path / "project", slug)
    game = tmp_path / "garrysmod"
    game.mkdir()
    (game / "gameinfo.txt").write_text("GameInfo {}", encoding="utf-8")
    result = install_to_gmod(addon, game, slug, "Selector Hash Test", "abc123def456", "token123")
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    selector_rel = f"lua/autorun/client/00_ember_{slug}_player.lua"
    model_rel = f"models/player/{slug}/{slug}.mdl"
    for rel in (selector_rel, model_rel):
        installed = game / rel
        assert rel in manifest["direct_files"]
        assert manifest["sha256"][rel] == hashlib.sha256(installed.read_bytes()).hexdigest()
    assert manifest["verification"]["dedicated_client_registration"] is True
    assert manifest["verification"]["all_direct_file_hashes_verified"] is True
