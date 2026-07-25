from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path


VERSION = "2.2.1"
_SLUG_RE = re.compile(r"^[a-z0-9_]{1,48}$")


def _lua_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _write_entity_icon(path: Path) -> None:
    width = height = 64
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        for x in range(width):
            border = x < 3 or y < 3 or x >= width - 3 or y >= height - 3
            head = (x - 32) ** 2 + (y - 19) ** 2 <= 9 ** 2
            torso = 21 <= x <= 43 and 29 <= y <= 52
            arms = (13 <= x <= 21 or 43 <= x <= 51) and 31 <= y <= 47
            legs = (22 <= x <= 29 or 35 <= x <= 42) and 50 <= y <= 61
            if border:
                rgba = (255, 122, 0, 255)
            elif head or torso or arms or legs:
                rgba = (255, 244, 228, 255)
            else:
                rgba = (20, 13, 8, 255)
            rows.extend(rgba)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    payload = b"\x89PNG\r\n\x1a\n"
    payload += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    payload += chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    payload += chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def ensure_runtime_addon_files(
    addon: Path,
    slug: str,
    display_name: str,
    *,
    project_id: str = "",
    build_token: str = "",
) -> dict[str, Path]:
    """Generate one self contained shared autorun and a spawnable ragdoll.

    Registration and validation intentionally live in the same autorun. Earlier
    releases could install a model binary without the client validator ever running,
    leaving a permanent NOT_RUN state and no selector entry.
    """
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError("Invalid internal slug for Garry's Mod runtime files.")

    lua_root = addon / "lua"
    autorun_dir = lua_root / "autorun"
    client_dir = autorun_dir / "client"
    entity_class = f"ember_{slug}_ragdoll"
    entity_dir = lua_root / "entities" / entity_class
    autorun_dir.mkdir(parents=True, exist_ok=True)
    client_dir.mkdir(parents=True, exist_ok=True)
    entity_dir.mkdir(parents=True, exist_ok=True)

    # Remove every historical file name so only one registration source can mount.
    for legacy in (
        autorun_dir / f"{slug}.lua",
        autorun_dir / f"ember_{slug}.lua",
        client_dir / f"{slug}_playermodel.lua",
        client_dir / f"{slug}_validation.lua",
        client_dir / f"ember_{slug}_validation.lua",
    ):
        legacy.unlink(missing_ok=True)

    model_path = f"models/player/{slug}/{slug}.mdl"
    entity_icon_material = f"entities/{entity_class}.png"
    entity_icon = addon / "materials" / entity_icon_material
    _write_entity_icon(entity_icon)

    registration = autorun_dir / f"ember_{slug}.lua"
    lines = [
        "if SERVER then AddCSLuaFile() end",
        "",
        f"local VERSION = {_lua_string(VERSION)}",
        f"local ID = {_lua_string(slug)}",
        f"local DISPLAY = {_lua_string(display_name)}",
        f"local MODEL = {_lua_string(model_path)}",
        f"local RAGDOLL_CLASS = {_lua_string(entity_class)}",
        f"local SPAWNLIST_HOOK = {_lua_string('EmberCharacterSpawnlist_' + slug)}",
        f"local RESULT_FILE = {_lua_string('ember_character_builder/runtime_' + slug + '.json')}",
        f"local PROJECT_ID = {_lua_string(project_id)}",
        f"local BUILD_TOKEN = {_lua_string(build_token)}",
        "",
        "if SERVER then",
        "    resource.AddFile(MODEL)",
        f"    resource.AddFile({_lua_string(f'models/player/{slug}/{slug}.vvd')})",
        f"    resource.AddFile({_lua_string(f'models/player/{slug}/{slug}.dx90.vtx')})",
        f"    resource.AddFile({_lua_string(f'models/player/{slug}/{slug}.phy')})",
        f"    resource.AddFile({_lua_string('materials/' + entity_icon_material)})",
        f"    local materialFiles = file.Find({_lua_string(f'materials/models/player/{slug}/*')}, 'GAME') or {{}}",
        "    for _, materialFile in ipairs(materialFiles) do",
        f"        resource.AddFile({_lua_string(f'materials/models/player/{slug}/')} .. materialFile)",
        "    end",
        "end",
        "",
        "local registrationErrors = {}",
        "local registrationAttempts = 0",
        "local function recordError(stage, value)",
        "    table.insert(registrationErrors, tostring(stage) .. ': ' .. tostring(value))",
        "end",
        "",
        "local function registerModel()",
        "    registrationAttempts = registrationAttempts + 1",
        "    local ok, err = pcall(function()",
        "        player_manager.AddValidModel(ID, MODEL)",
        '        player_manager.AddValidHands(ID, "models/weapons/c_arms_citizen.mdl", 0, "00000000")',
        "        if CLIENT then",
        '            list.Set("PlayerOptionsModel", DISPLAY, MODEL)',
        '            list.Set("PlayerOptionsModel", ID, MODEL)',
        '            language.Add("playermodel." .. ID, DISPLAY)',
        "        end",
        "    end)",
        "    if ok then registrationErrors = {} else recordError('registration', err) end",
        "    return ok",
        "end",
        "",
        "registerModel()",
        f'hook.Add("Initialize", "EmberRegisterInitialize_{slug}", registerModel)',
        f'hook.Add("InitPostEntity", "EmberRegisterPostEntity_{slug}", registerModel)',
        f'hook.Add("PopulatePlayerOptions", "EmberRegisterPlayerOptions_{slug}", registerModel)',
        "",
        "if CLIENT then",
        "    timer.Simple(0, registerModel)",
        "    timer.Simple(1, registerModel)",
        "    timer.Simple(5, registerModel)",
        "",
        f'    hook.Add("PopulatePropMenu", "EmberCharacterSpawnlist_{slug}", function()',
        "        local contents = {",
        "            { type = 'header', text = DISPLAY },",
        "            { type = 'model', model = MODEL, wide = 96, tall = 96 },",
        f"            {{ type = 'entity', spawnname = RAGDOLL_CLASS, nicename = DISPLAY .. ' Ragdoll', material = {_lua_string(entity_icon_material)} }},",
        "        }",
        f'        spawnmenu.AddPropCategory("ember_character_builder_{slug}", DISPLAY, contents, "icon16/user.png")',
        "    end)",
        "",
        "    local ACTIVITY_CHECKS = {",
        "        idle = ACT_HL2MP_IDLE, walk = ACT_HL2MP_WALK, run = ACT_HL2MP_RUN,",
        "        crouch_idle = ACT_HL2MP_IDLE_CROUCH, crouch_walk = ACT_HL2MP_WALK_CROUCH,",
        "        jump = ACT_HL2MP_JUMP, pistol = ACT_HL2MP_IDLE_PISTOL, rifle = ACT_HL2MP_IDLE_AR2,",
        "    }",
        "    local REQUIRED_BONES = {",
        "        'ValveBiped.Bip01_Pelvis', 'ValveBiped.Bip01_Spine', 'ValveBiped.Bip01_Spine1', 'ValveBiped.Bip01_Spine2',",
        "        'ValveBiped.Bip01_Spine4', 'ValveBiped.Bip01_Neck1', 'ValveBiped.Bip01_Head1', 'ValveBiped.forward',",
        "        'ValveBiped.Bip01_R_Clavicle', 'ValveBiped.Bip01_R_UpperArm', 'ValveBiped.Bip01_R_Forearm', 'ValveBiped.Bip01_R_Hand', 'ValveBiped.Anim_Attachment_RH',",
        "        'ValveBiped.Bip01_L_Clavicle', 'ValveBiped.Bip01_L_UpperArm', 'ValveBiped.Bip01_L_Forearm', 'ValveBiped.Bip01_L_Hand', 'ValveBiped.Anim_Attachment_LH',",
        "        'ValveBiped.Bip01_R_Thigh', 'ValveBiped.Bip01_R_Calf', 'ValveBiped.Bip01_R_Foot', 'ValveBiped.Bip01_R_Toe0',",
        "        'ValveBiped.Bip01_L_Thigh', 'ValveBiped.Bip01_L_Calf', 'ValveBiped.Bip01_L_Foot', 'ValveBiped.Bip01_L_Toe0',",
        "    }",
        "    local EXPECTED_PARENTS = {",
        "        ['ValveBiped.Bip01_Spine'] = 'ValveBiped.Bip01_Pelvis',",
        "        ['ValveBiped.Bip01_Spine1'] = 'ValveBiped.Bip01_Spine',",
        "        ['ValveBiped.Bip01_Spine2'] = 'ValveBiped.Bip01_Spine1',",
        "        ['ValveBiped.Bip01_Spine4'] = 'ValveBiped.Bip01_Spine2',",
        "        ['ValveBiped.Bip01_Neck1'] = 'ValveBiped.Bip01_Spine4',",
        "        ['ValveBiped.Bip01_Head1'] = 'ValveBiped.Bip01_Neck1',",
        "        ['ValveBiped.forward'] = 'ValveBiped.Bip01_Head1',",
        "        ['ValveBiped.Bip01_R_Clavicle'] = 'ValveBiped.Bip01_Spine4',",
        "        ['ValveBiped.Bip01_R_UpperArm'] = 'ValveBiped.Bip01_R_Clavicle',",
        "        ['ValveBiped.Bip01_R_Forearm'] = 'ValveBiped.Bip01_R_UpperArm',",
        "        ['ValveBiped.Bip01_R_Hand'] = 'ValveBiped.Bip01_R_Forearm',",
        "        ['ValveBiped.Anim_Attachment_RH'] = 'ValveBiped.Bip01_R_Hand',",
        "        ['ValveBiped.Bip01_L_Clavicle'] = 'ValveBiped.Bip01_Spine4',",
        "        ['ValveBiped.Bip01_L_UpperArm'] = 'ValveBiped.Bip01_L_Clavicle',",
        "        ['ValveBiped.Bip01_L_Forearm'] = 'ValveBiped.Bip01_L_UpperArm',",
        "        ['ValveBiped.Bip01_L_Hand'] = 'ValveBiped.Bip01_L_Forearm',",
        "        ['ValveBiped.Anim_Attachment_LH'] = 'ValveBiped.Bip01_L_Hand',",
        "        ['ValveBiped.Bip01_R_Thigh'] = 'ValveBiped.Bip01_Pelvis',",
        "        ['ValveBiped.Bip01_R_Calf'] = 'ValveBiped.Bip01_R_Thigh',",
        "        ['ValveBiped.Bip01_R_Foot'] = 'ValveBiped.Bip01_R_Calf',",
        "        ['ValveBiped.Bip01_R_Toe0'] = 'ValveBiped.Bip01_R_Foot',",
        "        ['ValveBiped.Bip01_L_Thigh'] = 'ValveBiped.Bip01_Pelvis',",
        "        ['ValveBiped.Bip01_L_Calf'] = 'ValveBiped.Bip01_L_Thigh',",
        "        ['ValveBiped.Bip01_L_Foot'] = 'ValveBiped.Bip01_L_Calf',",
        "        ['ValveBiped.Bip01_L_Toe0'] = 'ValveBiped.Bip01_L_Foot',",
        "    }",
        "",
        "    local function inspectModel(result)",
        "        local entity = ClientsideModel(MODEL, RENDERGROUP_OPAQUE)",
        "        if not IsValid(entity) then result.clientside_model_created = false return end",
        "        entity:SetNoDraw(true)",
        "        entity:SetupBones()",
        "        result.clientside_model_created = true",
        "        result.sequence_count = entity:GetSequenceCount()",
        "        result.sequence_count_valid = result.sequence_count > 8",
        "        result.activities = {}",
        "        result.activities_valid = true",
        "        for name, activity in pairs(ACTIVITY_CHECKS) do",
        "            local sequence = entity:SelectWeightedSequence(activity)",
        "            local valid = sequence ~= nil and sequence >= 0",
        "            result.activities[name] = { sequence = sequence, valid = valid }",
        "            result.activities_valid = result.activities_valid and valid",
        "        end",
        "        result.bones = {}",
        "        result.required_bones_valid = true",
        "        for _, name in ipairs(REQUIRED_BONES) do",
        "            local index = entity:LookupBone(name)",
        "            local valid = index ~= nil and index >= 0",
        "            result.bones[name] = valid",
        "            result.required_bones_valid = result.required_bones_valid and valid",
        "        end",
        "        result.materials = {}",
        "        result.material_error_count = 0",
        "        for _, materialName in ipairs(entity:GetMaterials() or {}) do",
        "            local material = Material(materialName)",
        "            local materialError = material:IsError()",
        '            local baseTexture = material:GetTexture("$basetexture")',
        "            local textureError = baseTexture == nil",
        "            if baseTexture ~= nil then",
        "                local errorTexture = baseTexture.IsErrorTexture ~= nil and baseTexture:IsErrorTexture() or false",
        "                local textureName = string.lower(baseTexture:GetName() or '')",
        "                textureError = errorTexture or textureName == 'error' or string.find(textureName, 'error', 1, true) ~= nil",
        "            end",
        "            if materialError or textureError then result.material_error_count = result.material_error_count + 1 end",
        "            table.insert(result.materials, { name = materialName, material_error = materialError, texture_error = textureError })",
        "        end",
        "        result.materials_valid = #result.materials > 0 and result.material_error_count == 0",
        "",
        "        local info = util.GetModelInfo(MODEL) or {}",
        "        result.model_info = { bone_count = info.BoneCount or 0, mesh_count = info.MeshCount or 0, static_prop = info.StaticProp == true }",
        "        result.model_info_valid = result.model_info.bone_count >= 26 and result.model_info.mesh_count > 0 and not result.model_info.static_prop",
        "        result.core_bone_hierarchy_valid = result.required_bones_valid",
        "        result.core_bone_hierarchy = {}",
        "        for childName, parentName in pairs(EXPECTED_PARENTS) do",
        "            local child = entity:LookupBone(childName)",
        "            local expectedParent = entity:LookupBone(parentName)",
        "            local actualParent = child ~= nil and entity:GetBoneParent(child) or -1",
        "            local valid = child ~= nil and expectedParent ~= nil and actualParent == expectedParent",
        "            result.core_bone_hierarchy[childName] = { parent = parentName, valid = valid }",
        "            result.core_bone_hierarchy_valid = result.core_bone_hierarchy_valid and valid",
        "        end",
        "        local pelvisIndex = entity:LookupBone('ValveBiped.Bip01_Pelvis')",
        "        if pelvisIndex == nil or entity:GetBoneParent(pelvisIndex) ~= -1 then result.core_bone_hierarchy_valid = false end",
        "",
        "        local renderMin, renderMax = entity:GetModelRenderBounds()",
        "        local renderSize = renderMax - renderMin",
        "        result.render_bounds = { min = { renderMin.x, renderMin.y, renderMin.z }, max = { renderMax.x, renderMax.y, renderMax.z }, size = { renderSize.x, renderSize.y, renderSize.z } }",
        "        result.render_bounds_valid = renderSize.x >= 30 and renderSize.x <= 110 and renderSize.y >= 2 and renderSize.y <= 65 and renderSize.z >= 48 and renderSize.z <= 105 and renderMin.z > -18 and renderMax.z > 48",
        "",
        "        local function bonePosition(name)",
        "            local index = entity:LookupBone(name)",
        "            if index == nil then return nil end",
        "            local matrix = entity:GetBoneMatrix(index)",
        "            if matrix == nil then return nil end",
        "            return matrix:GetTranslation()",
        "        end",
        "        local pelvis = bonePosition('ValveBiped.Bip01_Pelvis')",
        "        local head = bonePosition('ValveBiped.Bip01_Head1')",
        "        local leftHand = bonePosition('ValveBiped.Bip01_L_Hand')",
        "        local rightHand = bonePosition('ValveBiped.Bip01_R_Hand')",
        "        local leftFoot = bonePosition('ValveBiped.Bip01_L_Foot')",
        "        local rightFoot = bonePosition('ValveBiped.Bip01_R_Foot')",
        "        result.bone_geometry_valid = pelvis ~= nil and head ~= nil and leftHand ~= nil and rightHand ~= nil and leftFoot ~= nil and rightFoot ~= nil",
        "        if result.bone_geometry_valid then",
        "            local headHeight = head.z - pelvis.z",
        "            local handSpan = leftHand:Distance(rightHand)",
        "            local feetBelowPelvis = math.max(leftFoot.z, rightFoot.z) < pelvis.z - 18",
        "            result.bone_geometry = { head_height = headHeight, hand_span = handSpan, feet_below_pelvis = feetBelowPelvis }",
        "            result.bone_geometry_valid = headHeight > 20 and headHeight < 38 and handSpan > 42 and handSpan < 90 and feetBelowPelvis",
        "        end",
        "",
        "        local meshes = util.GetModelMeshes(MODEL, 0)",
        "        result.mesh_contract = { mesh_count = 0, vertex_count = 0, weight_records_seen = 0, invalid_weight_vertices = 0, min = nil, max = nil }",
        "        if istable(meshes) then",
        "            local meshMin, meshMax",
        "            for _, meshData in ipairs(meshes) do",
        "                result.mesh_contract.mesh_count = result.mesh_contract.mesh_count + 1",
        "                for _, vertex in ipairs(meshData.verticies or {}) do",
        "                    result.mesh_contract.vertex_count = result.mesh_contract.vertex_count + 1",
        "                    local pos = vertex.pos",
        "                    if pos ~= nil then",
        "                        if meshMin == nil then meshMin = Vector(pos.x, pos.y, pos.z) meshMax = Vector(pos.x, pos.y, pos.z) else",
        "                            meshMin.x = math.min(meshMin.x, pos.x) meshMin.y = math.min(meshMin.y, pos.y) meshMin.z = math.min(meshMin.z, pos.z)",
        "                            meshMax.x = math.max(meshMax.x, pos.x) meshMax.y = math.max(meshMax.y, pos.y) meshMax.z = math.max(meshMax.z, pos.z)",
        "                        end",
        "                    end",
        "                    if istable(vertex.weights) and #vertex.weights > 0 then",
        "                        result.mesh_contract.weight_records_seen = result.mesh_contract.weight_records_seen + 1",
        "                        local total = 0",
        "                        for _, weight in ipairs(vertex.weights) do total = total + (tonumber(weight.weight) or 0) end",
        "                        if #vertex.weights > 3 or math.abs(total - 1) > 0.002 then result.mesh_contract.invalid_weight_vertices = result.mesh_contract.invalid_weight_vertices + 1 end",
        "                    end",
        "                end",
        "            end",
        "            if meshMin ~= nil then",
        "                local meshSize = meshMax - meshMin",
        "                result.mesh_contract.min = { meshMin.x, meshMin.y, meshMin.z }",
        "                result.mesh_contract.max = { meshMax.x, meshMax.y, meshMax.z }",
        "                result.mesh_contract.size = { meshSize.x, meshSize.y, meshSize.z }",
        "                result.mesh_contract.bounds_valid = meshSize.x >= 30 and meshSize.x <= 110 and meshSize.y >= 2 and meshSize.y <= 65 and meshSize.z >= 48 and meshSize.z <= 105",
        "            end",
        "        end",
        "        result.mesh_weight_diagnostic_valid = result.mesh_contract.weight_records_seen == 0 or result.mesh_contract.invalid_weight_vertices == 0",
        "        result.mesh_contract_valid = result.mesh_contract.mesh_count > 0 and result.mesh_contract.vertex_count > 1000 and result.mesh_contract.bounds_valid == true",
        "        result.physics_file_exists = file.Exists(string.Replace(MODEL, '.mdl', '.phy'), 'GAME')",
        "        local ragdoll = ClientsideRagdoll(MODEL, RENDERGROUP_OPAQUE)",
        "        result.clientside_ragdoll_created = IsValid(ragdoll)",
        "        result.physics_bone_count = result.clientside_ragdoll_created and ragdoll:GetPhysicsObjectCount() or 0",
        "        result.physics_contract_valid = result.physics_file_exists and result.clientside_ragdoll_created and result.physics_bone_count >= 10",
        "        if IsValid(ragdoll) then ragdoll:Remove() end",
        "        entity:Remove()",
        "    end",
        "",
        "    local function collect()",
        "        registerModel()",
        '        local options = list.Get("PlayerOptionsModel") or {}',
        "        local translated = player_manager.TranslatePlayerModel(ID)",
        "        local validModels = player_manager.AllValidModels() or {}",
        "        local hands = player_manager.TranslatePlayerHands(ID) or {}",
        "        local stored = scripted_ents.GetStored(RAGDOLL_CLASS)",
        "        local propHooks = (hook.GetTable() or {}).PopulatePropMenu or {}",
        "        local selectorModel = options[DISPLAY] or options[ID]",
        "        local result = {",
        "            version = VERSION, project_id = PROJECT_ID, build_token = BUILD_TOKEN,",
        "            id = ID, display_name = DISPLAY, model = MODEL, checked_at = os.time(),",
        "            registration_errors = registrationErrors, registration_error_count = #registrationErrors, registration_attempts = registrationAttempts,",
        '            model_file_exists = file.Exists(MODEL, "GAME"),',
        "            valid_model = util.IsValidModel(MODEL),",
        "            translated_model = translated, translated_model_matches = translated == MODEL,",
        "            all_valid_models_model = validModels[ID], all_valid_models_matches = validModels[ID] == MODEL,",
        "            player_options_model = selectorModel, player_options_matches = selectorModel == MODEL,",
        "            hands_model = hands.model, hands_model_matches = hands.model == 'models/weapons/c_arms_citizen.mdl',",
        "            ragdoll_entity_registered = stored ~= nil,",
        "            spawnlist_hook_registered = propHooks[SPAWNLIST_HOOK] ~= nil,",
        "        }",
        "        local ok, err = pcall(inspectModel, result)",
        "        if not ok then result.inspect_error = tostring(err) result.clientside_model_created = false end",
        "        result.passed = result.registration_error_count == 0 and result.model_file_exists and result.valid_model",
        "            and result.translated_model_matches and result.all_valid_models_matches and result.player_options_matches",
        "            and result.hands_model_matches and result.ragdoll_entity_registered and result.spawnlist_hook_registered",
        "            and result.clientside_model_created and result.sequence_count_valid and result.activities_valid",
        "            and result.required_bones_valid and result.materials_valid and result.model_info_valid",
        "            and result.core_bone_hierarchy_valid and result.render_bounds_valid and result.bone_geometry_valid",
        "            and result.mesh_contract_valid and result.physics_contract_valid",
        "        result.status = result.passed and 'passed' or 'failed'",
        "        return result",
        "    end",
        "",
        "    local function persist(verbose)",
        "        local ok, result = pcall(collect)",
        "        if not ok then",
        "            result = { version = VERSION, project_id = PROJECT_ID, build_token = BUILD_TOKEN, status = 'validator_error', passed = false, error = tostring(result), checked_at = os.time() }",
        "        end",
        '        file.CreateDir("ember_character_builder")',
        "        file.Write(RESULT_FILE, util.TableToJSON(result, true))",
        "        if verbose then print('[EMBER] Runtime validation for ' .. DISPLAY) PrintTable(result) end",
        "        return result",
        "    end",
        "",
        "    local function scheduleValidation()",
        "        timer.Simple(1, function() persist(false) end)",
        "        timer.Simple(5, function() persist(false) end)",
        "        timer.Simple(12, function() persist(false) end)",
        "    end",
        f'    hook.Add("InitPostEntity", "EmberAutoValidate_{slug}", scheduleValidation)',
        f'    hook.Add("OnReloaded", "EmberReloadValidate_{slug}", scheduleValidation)',
        "    timer.Simple(2, function() persist(false) end)",
        "    timer.Simple(8, function() persist(false) end)",
        f'    concommand.Add("ember_validate_{slug}", function() persist(true) end)',
        f'    concommand.Add("ember_apply_{slug}", function()',
        '        RunConsoleCommand("cl_playermodel", ID)',
        "        chat.AddText(Color(255, 122, 0), 'EMBER: selected ' .. DISPLAY .. '. Respawn to apply.')",
        "    end)",
        f'    concommand.Add("ember_preview_{slug}", function()',
        '        local frame = vgui.Create("DFrame")',
        "        frame:SetSize(math.min(ScrW() - 40, 920), math.min(ScrH() - 40, 720))",
        "        frame:Center() frame:SetTitle('EMBER PLAYER MODEL PREVIEW / ' .. DISPLAY) frame:MakePopup()",
        '        local panel = vgui.Create("DModelPanel", frame)',
        "        panel:Dock(FILL) panel:SetModel(MODEL) panel:SetFOV(34)",
        "        panel:SetCamPos(Vector(110, 0, 58)) panel:SetLookAt(Vector(0, 0, 44)) panel:SetAnimated(true)",
        "        persist(true)",
        "    end)",
        "end",
        "",
        f'print("[EMBER] Loaded player model registration {slug} -> " .. MODEL)',
    ]
    registration.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # A dedicated client autorun is installed as a second, idempotent registration
    # route.  The shared autorun remains authoritative for validation and server
    # resources, while this file guarantees that both the stock C menu and Enhanced
    # PlayerModel Selector see the model even when they populate before InitPostEntity.
    client_registration = client_dir / f"00_ember_{slug}_player.lua"
    client_registration.write_text("\n".join([
        f"local ID = {_lua_string(slug)}",
        f"local DISPLAY = {_lua_string(display_name)}",
        f"local MODEL = {_lua_string(model_path)}",
        "local function register()",
        "    player_manager.AddValidModel(ID, MODEL)",
        '    player_manager.AddValidHands(ID, "models/weapons/c_arms_citizen.mdl", 0, "00000000")',
        '    list.Set("PlayerOptionsModel", ID, MODEL)',
        '    list.Set("PlayerOptionsModel", DISPLAY, MODEL)',
        '    language.Add("playermodel." .. ID, DISPLAY)',
        "end",
        "register()",
        f'hook.Add("Initialize", "EmberClientRegisterInitialize_{slug}", register)',
        f'hook.Add("InitPostEntity", "EmberClientRegisterPostEntity_{slug}", register)',
        f'hook.Add("PopulatePlayerOptions", "EmberClientRegisterOptions_{slug}", register)',
        "timer.Simple(0, register)",
        "timer.Simple(1, register)",
        "timer.Simple(5, register)",
        f'print("[EMBER] Client player selector registration loaded: {slug}")',
    ]) + "\n", encoding="utf-8")

    shared = entity_dir / "shared.lua"
    shared.write_text(
        "\n".join([
            "if SERVER then AddCSLuaFile() end",
            'ENT.Type = "anim"',
            'ENT.Base = "base_gmodentity"',
            f"ENT.PrintName = {_lua_string(display_name + ' Ragdoll')}",
            'ENT.Category = "Ember Character Builder"',
            'ENT.Author = "Ember Guided Character Builder"',
            'ENT.Purpose = "Spawns the compiled model as a physics ragdoll."',
            "ENT.Spawnable = true",
            "ENT.AdminOnly = false",
            f"ENT.IconOverride = {_lua_string(entity_icon_material)}",
            f"ENT.ModelPath = {_lua_string(model_path)}",
            "",
            "function ENT:SpawnFunction(ply, trace)",
            "    if not trace.Hit then return end",
            '    local ragdoll = ents.Create("prop_ragdoll")',
            "    if not IsValid(ragdoll) then return end",
            "    ragdoll:SetModel(self.ModelPath)",
            "    ragdoll:SetPos(trace.HitPos + trace.HitNormal * 8)",
            "    ragdoll:SetAngles(Angle(0, IsValid(ply) and ply:EyeAngles().y + 180 or 0, 0))",
            "    ragdoll:Spawn() ragdoll:Activate()",
            "    return ragdoll",
            "end",
        ]) + "\n",
        encoding="utf-8",
    )
    entity_init = entity_dir / "init.lua"
    entity_init.write_text('AddCSLuaFile("cl_init.lua")\nAddCSLuaFile("shared.lua")\ninclude("shared.lua")\n', encoding="utf-8")
    entity_client = entity_dir / "cl_init.lua"
    entity_client.write_text('include("shared.lua")\n', encoding="utf-8")

    addon_json = addon / "addon.json"
    addon_json.write_text(json.dumps({
        "title": display_name,
        "type": "model",
        "tags": ["fun", "realism"],
        "ignore": ["*.blend", "*.smd", "*.qc", "*.qci", "*.tga", "reports/*"],
    }, indent=2), encoding="utf-8")

    return {
        "registration": registration,
        "client_registration": client_registration,
        "validation": registration,
        "entity_shared": shared,
        "entity_init": entity_init,
        "entity_client": entity_client,
        "addon_json": addon_json,
        "entity_icon": entity_icon,
    }
