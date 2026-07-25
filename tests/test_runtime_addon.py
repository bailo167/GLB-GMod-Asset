from pathlib import Path

from ember_gmod.runtime_addon import ensure_runtime_addon_files


def test_runtime_addon_registers_and_strictly_validates_player_model(tmp_path: Path):
    addon = tmp_path / "addon"
    paths = ensure_runtime_addon_files(addon, "test_character", "Test Character", project_id="abc123def456", build_token="token123")
    registration = paths["registration"].read_text(encoding="utf-8")
    entity = paths["entity_shared"].read_text(encoding="utf-8")
    validation = paths["validation"].read_text(encoding="utf-8")
    assert 'local ID = "test_character"' in registration
    assert 'local MODEL = "models/player/test_character/test_character.mdl"' in registration
    assert "player_manager.AddValidModel(ID, MODEL)" in registration
    assert "resource.AddFile(MODEL)" in registration
    assert registration.index("player_manager.AddValidModel") < registration.index("if CLIENT then")
    assert 'list.Set("PlayerOptionsModel", ID, MODEL)' in registration
    assert "spawnmenu.AddPropCategory" in registration
    assert "ENT.Spawnable = true" in entity
    assert 'ents.Create("prop_ragdoll")' in entity
    assert "ClientsideModel" in validation
    assert "GetSequenceCount" in validation
    assert "SelectWeightedSequence" in validation
    assert "ACT_HL2MP_RUN" in validation
    assert "required_bones_valid" in validation
    assert "material_error_count" in validation
    assert "materials_valid" in validation
    assert "core_bone_hierarchy_valid" in validation
    assert "ClientsideRagdoll" in validation
    assert "physics_contract_valid" in validation
    assert "info.SequenceCount" not in validation
    assert "info.MaterialCount" not in validation
    assert "lookup ~= index - 1" not in validation
    assert "IsErrorTexture" in validation
    assert "runtime_test_character.json" in validation
    assert 'local PROJECT_ID = "abc123def456"' in validation
    assert 'local BUILD_TOKEN = "token123"' in validation
    assert paths["entity_icon"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_runtime_addon_rejects_unsafe_slug(tmp_path: Path):
    try:
        ensure_runtime_addon_files(tmp_path / "addon", "../unsafe", "Unsafe")
    except ValueError:
        pass
    else:
        raise AssertionError("Unsafe slug was accepted")


def test_runtime_addon_has_multiple_selector_and_automatic_proof_routes(tmp_path: Path):
    addon = tmp_path / "addon"
    paths = ensure_runtime_addon_files(addon, "route_test", "Route Test", project_id="abc123def456", build_token="token123")
    shared = paths["registration"].read_text(encoding="utf-8")
    client = paths["client_registration"].read_text(encoding="utf-8")
    assert 'hook.Add("PopulatePlayerOptions"' in shared
    assert 'hook.Add("PopulatePlayerOptions"' in client
    assert "timer.Simple(0, registerModel)" in shared
    assert "timer.Simple(5, registerModel)" in shared
    assert "timer.Simple(0, register)" in client
    assert "timer.Simple(5, register)" in client
    assert 'hook.Add("InitPostEntity", "EmberAutoValidate_route_test"' in shared
    assert 'hook.Add("OnReloaded", "EmberReloadValidate_route_test"' in shared
    assert "timer.Simple(2, function() persist(false) end)" in shared
    assert "timer.Simple(8, function() persist(false) end)" in shared
    assert "registrationErrors = {}" in shared
    assert "local materialFiles = file.Find" in shared
