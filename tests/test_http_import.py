from __future__ import annotations

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import app
from ember_gmod.config import ConfigStore
from ember_gmod.jobs import JobManager
from ember_gmod.project import ProjectStore
from test_project import glb_bytes


def multipart(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> tuple[bytes, str]:
    boundary = "----EmberBoundary7MA4YWxk"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(), b"\r\n",
        ])
    for name, (filename, data, mime) in files.items():
        chunks.extend([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            data, b"\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def request_json(url: str, body: bytes | None = None, content_type: str | None = None):
    headers = {"Content-Type": content_type} if content_type else {}
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_http_glb_inspection_and_project_creation(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    projects = ProjectStore(workspace)
    config = ConfigStore(workspace / "toolchain.json")
    jobs = JobManager(tmp_path, projects, config.load)
    monkeypatch.setattr(app, "WORKSPACE", workspace)
    monkeypatch.setattr(app, "PROJECTS", projects)
    monkeypatch.setattr(app, "CONFIG_STORE", config)
    monkeypatch.setattr(app, "JOBS", jobs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status, health = request_json(base + "/api/health")
        assert status == 200
        assert health["version"] == "2.2.3"

        inspect_body, inspect_type = multipart({}, {"glb": ("sample.glb", glb_bytes(), "model/gltf-binary")})
        status, inspection = request_json(base + "/api/inspect", inspect_body, inspect_type)
        assert status == 200
        assert inspection["filename"] == "sample.glb"
        assert inspection["meshes"] == 1

        options = json.dumps({"display_name": "HTTP Import", "slug": "http_import", "quality": "good"})
        project_body, project_type = multipart(
            {"options": options},
            {"glb": ("sample.glb", glb_bytes(), "model/gltf-binary")},
        )
        status, project = request_json(base + "/api/projects", project_body, project_type)
        assert status == 201
        assert project["options"]["slug"] == "http_import"
        assert project["state"] == "guide_required"
        assert project["guide_validation"]["assigned_required"] == 0
        assert (projects.project_dir(project["id"]) / "source" / "model.glb").is_file()
        # The runtime route reports not_run until Garry's Mod has executed the generated client validator.
        game = tmp_path / "garrysmod"
        game.mkdir()
        (game / "gameinfo.txt").write_text("GameInfo {}", encoding="utf-8")
        cfg = config.load()
        cfg.game_dir = str(game)
        config.save(cfg)
        status, runtime = request_json(base + f"/api/projects/{project['id']}/runtime-check")
        assert status == 200
        assert runtime["status"] == "not_run"
        current = projects.load(project["id"])
        current.last_job_id = "buildtoken01"
        projects.save(current)
        result_dir = game / "data" / "ember_character_builder"
        result_dir.mkdir(parents=True)
        result_file = result_dir / "runtime_http_import.json"
        result_file.write_text(json.dumps({
            "version": "2.2.3",
            "project_id": project["id"],
            "build_token": "wrongtoken",
            "passed": True,
            "valid_model": True,
        }), encoding="utf-8")
        status, runtime = request_json(base + f"/api/projects/{project['id']}/runtime-check")
        assert runtime["status"] == "stale"
        assert runtime["project_state"] != "complete"
        result_file.write_text(json.dumps({
            "version": "2.2.3",
            "project_id": project["id"],
            "build_token": "buildtoken01",
            "status": "passed",
            "passed": True,
            "model_file_exists": True,
            "valid_model": True,
            "translated_model_matches": True,
            "all_valid_models_matches": True,
            "player_options_matches": True,
            "hands_model_matches": True,
            "ragdoll_entity_registered": True,
            "spawnlist_hook_registered": True,
            "clientside_model_created": True,
            "sequence_count_valid": True,
            "activities_valid": True,
            "required_bones_valid": True,
            "materials_valid": True,
            "model_info_valid": True,
            "core_bone_hierarchy_valid": True,
            "render_bounds_valid": True,
            "bone_geometry_valid": True,
            "mesh_contract_valid": True,
            "physics_contract_valid": True,
            "registration_error_count": 0,
        }), encoding="utf-8")
        status, runtime = request_json(base + f"/api/projects/{project['id']}/runtime-check")
        assert runtime["status"] == "passed"
        assert runtime["project_state"] == "complete"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
