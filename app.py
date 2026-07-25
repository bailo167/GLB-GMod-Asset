from __future__ import annotations

import argparse
import io
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ember_gmod.config import ConfigStore, ToolchainConfig, tool_status
from ember_gmod.glb import GLBError, inspect_glb, validate_glb_bytes
from ember_gmod.jobs import JobManager
from ember_gmod.project import BuildOptions, ProjectStore
from ember_gmod.installer import install_to_gmod

APP_ROOT = Path(__file__).resolve().parent
WEB_ROOT = APP_ROOT / "web"
WORKSPACE = APP_ROOT / "workspace"
CONFIG_STORE = ConfigStore(WORKSPACE / "toolchain.json")
PROJECTS = ProjectStore(WORKSPACE)
JOBS = JobManager(APP_ROOT, PROJECTS, CONFIG_STORE.load)
MAX_BODY = 768 * 1024 * 1024


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def parse_multipart(headers, body: bytes) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    content_type = headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Expected multipart/form-data")
    raw = b"Content-Type: " + content_type.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    message = BytesParser(policy=default).parsebytes(raw)
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes, str]] = {}
    for part in message.iter_parts():
        disposition = part.get("Content-Disposition", "")
        params = dict(part.get_params(header="Content-Disposition", failobj=[]))
        name = params.get("name")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = params.get("filename")
        if filename:
            files[str(name)] = (Path(str(filename)).name, payload, part.get_content_type())
        else:
            charset = part.get_content_charset() or "utf-8"
            fields[str(name)] = payload.decode(charset, errors="replace")
    return fields, files


class Handler(BaseHTTPRequestHandler):
    server_version = "EmberGModBuilder/2.2.4"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write("[HTTP] " + fmt % args + "\n")

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, value: Any, status: int = 200) -> None:
        self._send(status, json_bytes(value), "application/json; charset=utf-8")

    def send_error_json(self, status: int, message: str, detail: str | None = None) -> None:
        payload = {"error": message}
        if detail:
            payload["detail"] = detail
        self.send_json(payload, status)

    def read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return b""
        if length > MAX_BODY:
            raise ValueError("Upload is larger than the 768 MB local safety limit.")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        try:
            parsed = urllib.parse.urlparse(self.path)
            path = parsed.path
            if path == "/api/health":
                cfg = CONFIG_STORE.load()
                self.send_json({
                    "name": "Ember Guided GMod Character Builder",
                    "version": "2.2.4",
                    "workspace": str(WORKSPACE),
                    "toolchain": tool_status(cfg),
                })
                return
            if path == "/api/config":
                cfg = CONFIG_STORE.load()
                self.send_json({"config": cfg.to_dict(), "status": tool_status(cfg)})
                return
            if path == "/api/projects":
                self.send_json({"projects": PROJECTS.list()})
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})", path)
            if m:
                self.send_json(PROJECTS.load(m.group(1)).to_dict())
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/source", path)
            if m:
                record = PROJECTS.load(m.group(1))
                root = PROJECTS.project_dir(record.id)
                source = (root / record.source_glb).resolve()
                if root.resolve() not in source.parents or not source.is_file():
                    self.send_error_json(404, "Project GLB is missing.")
                    return
                self._send(200, source.read_bytes(), "model/gltf-binary", {"Content-Disposition": f'inline; filename="{record.options.slug}.glb"'})
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/guide", path)
            if m:
                record = PROJECTS.load(m.group(1))
                self.send_json({"guide": record.guide, "validation": record.to_dict()["guide_validation"]})
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/files", path)
            if m:
                self.send_json({"files": PROJECTS.list_files(m.group(1))})
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/report", path)
            if m:
                root = PROJECTS.project_dir(m.group(1))
                report = root / "reports" / "build_report.json"
                if not report.exists():
                    self.send_json({"status": "not_run", "post_build": {}})
                else:
                    self.send_json(json.loads(report.read_text(encoding="utf-8")))
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/texture-proof", path)
            if m:
                record = PROJECTS.load(m.group(1))
                view = urllib.parse.parse_qs(parsed.query).get("view", ["front"])[0]
                if view not in {"front", "back"}:
                    self.send_error_json(400, "The texture proof view must be front or back.")
                    return
                root = PROJECTS.project_dir(record.id)
                proof = (root / "generated" / f"{record.options.slug}_texture_proof_{view}.png").resolve()
                if root.resolve() not in proof.parents or not proof.is_file():
                    self.send_error_json(404, "No baked material proof render exists for this project yet.")
                    return
                self._send(200, proof.read_bytes(), "image/png", {"Content-Disposition": f'inline; filename="{proof.name}"'})
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/runtime-check", path)
            if m:
                record = PROJECTS.load(m.group(1))
                cfg = CONFIG_STORE.load()
                game_dir = Path(cfg.game_dir).expanduser() if cfg.game_dir else None
                if not game_dir or not game_dir.is_dir():
                    self.send_error_json(409, "Configure the Garry's Mod game directory before reading the runtime check.")
                    return
                result_path = game_dir / "data" / "ember_character_builder" / f"runtime_{record.options.slug}.json"
                if not result_path.is_file():
                    self.send_json({"status": "not_run", "passed": False, "path": str(result_path)})
                    return
                try:
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    self.send_error_json(409, "Garry's Mod runtime check is not valid JSON.", str(result_path))
                    return
                project_matches = str(result.get("project_id", "")) == record.id
                build_matches = bool(record.last_job_id) and str(result.get("build_token", "")) == str(record.last_job_id)
                version_matches = str(result.get("version", "")) == "2.2.4"
                result["project_matches"] = project_matches
                result["build_matches"] = build_matches
                result["version_matches"] = version_matches
                stale = not (project_matches and build_matches and version_matches)
                waiting = str(result.get("status", "")) == "installed_waiting_for_game"
                required_runtime_checks = (
                    "model_file_exists", "valid_model", "translated_model_matches", "all_valid_models_matches",
                    "player_options_matches", "hands_model_matches", "ragdoll_entity_registered", "spawnlist_hook_registered",
                    "clientside_model_created", "sequence_count_valid", "activities_valid", "required_bones_valid",
                    "materials_valid", "model_info_valid", "core_bone_hierarchy_valid", "render_bounds_valid",
                    "bone_geometry_valid", "mesh_contract_valid", "physics_contract_valid",
                )
                missing_runtime_checks = [name for name in required_runtime_checks if result.get(name) is not True]
                result["missing_runtime_checks"] = missing_runtime_checks
                passed = bool(result.get("passed")) and not missing_runtime_checks and not stale and not waiting
                result["status"] = "stale" if stale else ("waiting_for_game" if waiting else ("passed" if passed else "failed"))
                result["path"] = str(result_path)
                if stale or waiting:
                    result["project_state"] = record.state
                else:
                    record.state = "complete" if passed else "runtime_failed"
                    PROJECTS.save(record)
                    result["project_state"] = record.state
                self.send_json(result)
                return
            m = re.fullmatch(r"/api/jobs/([a-f0-9]{12})", path)
            if m:
                self.send_json(JOBS.get(m.group(1)))
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/download", path)
            if m:
                record = PROJECTS.load(m.group(1))
                if not record.artifact_zip:
                    self.send_error_json(404, "No packaged output exists for this project.")
                    return
                root = PROJECTS.project_dir(record.id)
                artifact = (root / record.artifact_zip).resolve()
                if root.resolve() not in artifact.parents or not artifact.exists():
                    self.send_error_json(404, "Packaged output file is missing.")
                    return
                data = artifact.read_bytes()
                self._send(200, data, "application/zip", {"Content-Disposition": f'attachment; filename="{artifact.name}"'})
                return
            self.serve_static(path)
        except KeyError:
            self.send_error_json(404, "Requested job was not found.")
        except FileNotFoundError:
            self.send_error_json(404, "Requested project was not found.")
        except Exception as exc:
            self.send_error_json(500, "Request failed.", str(exc))

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self.read_body()
            if path == "/api/config":
                raw = json.loads(body.decode("utf-8")) if body else {}
                cfg = ToolchainConfig(**{k: str(raw.get(k, "")) for k in ToolchainConfig.__annotations__})
                CONFIG_STORE.save(cfg)
                self.send_json({"config": cfg.to_dict(), "status": tool_status(cfg)})
                return
            if path == "/api/inspect":
                _fields, files = parse_multipart(self.headers, body)
                if "glb" not in files:
                    self.send_error_json(400, "Attach a GLB file in the glb field.")
                    return
                filename, data, _mime = files["glb"]
                validate_glb_bytes(data)
                with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as fh:
                    fh.write(data)
                    temp_path = Path(fh.name)
                try:
                    report = inspect_glb(temp_path).to_dict()
                    report["filename"] = filename
                finally:
                    temp_path.unlink(missing_ok=True)
                self.send_json(report)
                return
            if path == "/api/projects":
                fields, files = parse_multipart(self.headers, body)
                if "glb" not in files:
                    self.send_error_json(400, "Attach a GLB file before creating a project.")
                    return
                try:
                    raw_options = json.loads(fields.get("options", "{}"))
                except json.JSONDecodeError:
                    self.send_error_json(400, "Project options are not valid JSON.")
                    return
                options = BuildOptions.from_dict(raw_options)
                glb_name, glb_data, _ = files["glb"]
                ref = files.get("reference_smd")
                record = PROJECTS.create(
                    io.BytesIO(glb_data),
                    glb_name,
                    options,
                    io.BytesIO(ref[1]) if ref else None,
                    ref[0] if ref else None,
                )
                self.send_json(record.to_dict(), 201)
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/build", path)
            if m:
                project_id = m.group(1)
                record = PROJECTS.load(project_id)
                validation = record.to_dict()["guide_validation"]
                if not record.guide.get("locked") or validation.get("errors"):
                    self.send_error_json(409, "Lock a complete landmark guide before building.", "; ".join(validation.get("errors", [])))
                    return
                job = JOBS.create_build(project_id)
                self.send_json(job.to_dict(), 202)
                return
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/install", path)
            if m:
                project_id = m.group(1)
                record = PROJECTS.load(project_id)
                cfg = CONFIG_STORE.load()
                game_dir = Path(cfg.game_dir).expanduser() if cfg.game_dir else None
                if not game_dir or not game_dir.is_dir():
                    self.send_error_json(409, "Configure the Garry's Mod game directory before installing.")
                    return
                project_root = PROJECTS.project_dir(project_id)
                addon_source = project_root / "addon"
                try:
                    result = install_to_gmod(
                        addon_source=addon_source,
                        game_dir=game_dir,
                        slug=record.options.slug,
                        display_name=record.options.display_name,
                        project_id=project_id,
                        build_token=record.last_job_id or "",
                    )
                except RuntimeError as exc:
                    self.send_error_json(409, "Install verification failed.", str(exc))
                    return
                report_path = project_root / "reports" / "build_report.json"
                if report_path.exists():
                    try:
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                        report.setdefault("post_build", {})["installed_to"] = result["addon_path"]
                        report["post_build"]["direct_install_manifest"] = result["manifest"]
                        report["post_build"]["install_verification"] = result["verification"]
                        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
                    except (OSError, json.JSONDecodeError):
                        pass
                record.state = "installed_unverified"
                PROJECTS.save(record)
                result["project_state"] = record.state
                self.send_json(result)
                return
            self.send_error_json(404, "Unknown API route.")
        except GLBError as exc:
            self.send_error_json(400, "The uploaded file is not a valid GLB.", str(exc))
        except ValueError as exc:
            self.send_error_json(400, str(exc))
        except FileNotFoundError:
            self.send_error_json(404, "Requested project was not found.")
        except Exception as exc:
            self.send_error_json(500, "Request failed.", str(exc))

    def do_PUT(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            body = self.read_body()
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})/guide", path)
            if not m:
                self.send_error_json(404, "Unknown API route.")
                return
            raw = json.loads(body.decode("utf-8")) if body else {}
            if not isinstance(raw, dict):
                self.send_error_json(400, "Guide payload must be a JSON object.")
                return
            record = PROJECTS.update_guide(m.group(1), raw)
            self.send_json(record.to_dict())
        except json.JSONDecodeError:
            self.send_error_json(400, "Guide payload is not valid JSON.")
        except FileNotFoundError:
            self.send_error_json(404, "Requested project was not found.")
        except Exception as exc:
            self.send_error_json(500, "Guide update failed.", str(exc))

    def do_DELETE(self) -> None:
        try:
            path = urllib.parse.urlparse(self.path).path
            m = re.fullmatch(r"/api/projects/([a-f0-9]{12})", path)
            if not m:
                self.send_error_json(404, "Unknown API route.")
                return
            PROJECTS.delete(m.group(1))
            self.send_json({"deleted": True})
        except FileNotFoundError:
            self.send_error_json(404, "Requested project was not found.")
        except Exception as exc:
            self.send_error_json(500, "Delete failed.", str(exc))

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            path = "/index.html"
        relative = Path(urllib.parse.unquote(path.lstrip("/")))
        if any(part == ".." for part in relative.parts):
            self.send_error_json(403, "Invalid path.")
            return
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in target.parents and target != WEB_ROOT.resolve():
            self.send_error_json(403, "Invalid path.")
            return
        if not target.is_file():
            target = WEB_ROOT / "index.html"
        content_type, _ = mimetypes.guess_type(target.name)
        self._send(200, target.read_bytes(), (content_type or "application/octet-stream") + ("; charset=utf-8" if target.suffix in {".html", ".css", ".js"} else ""))


def find_port(host: str, preferred: int) -> int:
    for port in range(preferred, preferred + 30):
        with socket.socket() as sock:
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port was found.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Ember Guided GMod Character Builder")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    port = find_port(args.host, args.port)
    server = ThreadingHTTPServer((args.host, port), Handler)
    url = f"http://{args.host}:{port}/"
    print(f"Ember Guided GMod Character Builder running at {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
