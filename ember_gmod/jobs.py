from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import traceback
import queue
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .config import ToolchainConfig
from .project import ProjectStore
from .runtime_addon import ensure_runtime_addon_files
from .installer import install_to_gmod
from .vtf_writer import inspect_vtf, write_vtf_from_tga


@dataclass
class Job:
    id: str
    project_id: str
    state: str
    phase: str
    progress: int
    created: float
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    def __init__(self, app_root: Path, store: ProjectStore, config_provider: Callable[[], ToolchainConfig]):
        self.app_root = app_root
        self.store = store
        self.config_provider = config_provider
        self.jobs: dict[str, Job] = {}
        self.logs: dict[str, list[str]] = {}
        self.lock = threading.Lock()

    def create_build(self, project_id: str) -> Job:
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id, project_id, "queued", "Queued", 0, time.time())
        with self.lock:
            self.jobs[job_id] = job
            self.logs[job_id] = []
        record = self.store.load(project_id)
        record.last_job_id = job_id
        record.state = "building"
        self.store.save(record)
        threading.Thread(target=self._run_build, args=(job_id,), daemon=True).start()
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                raise KeyError(job_id)
            result = job.to_dict()
            result["logs"] = list(self.logs.get(job_id, []))[-800:]
            return result

    def _set(self, job_id: str, **kwargs: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            for key, value in kwargs.items():
                setattr(job, key, value)

    def _log(self, job_id: str, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"[{stamp}] {message.rstrip()}"
        with self.lock:
            self.logs.setdefault(job_id, []).append(line)
            self.logs[job_id] = self.logs[job_id][-2500:]

    def _run_command(
        self,
        job_id: str,
        command: list[str],
        cwd: Path,
        timeout: float | None = None,
        progress_label: str | None = None,
    ) -> int:
        self._log(job_id, "$ " + " ".join(f'"{value}"' if " " in value else value for value in command))
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            try:
                for line in process.stdout:
                    lines.put(line)
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        started = time.monotonic()
        stream_closed = False
        last_progress_bucket = -1

        def emit_process_line(raw: str) -> None:
            nonlocal last_progress_bucket
            text_line = raw.strip()
            if not text_line:
                return
            if progress_label:
                match = re.fullmatch(r"(\d+)\s+(\d+)\s+(\d+)", text_line)
                if match:
                    total = int(match.group(1))
                    completed = int(match.group(3))
                    if total > 0:
                        percent = max(0, min(100, int(completed * 100 / total)))
                        bucket = percent // 10
                        if bucket > last_progress_bucket:
                            last_progress_bucket = bucket
                            self._log(job_id, f"{progress_label}: {percent}%")
                        return
            self._log(job_id, text_line)

        while True:
            if timeout is not None and time.monotonic() - started > timeout:
                process.kill()
                self._log(job_id, f"Command timed out after {int(timeout)} seconds and was terminated.")
                reader.join(timeout=2)
                while not lines.empty():
                    line = lines.get_nowait()
                    if line is not None:
                        emit_process_line(line)
                return 124
            try:
                line = lines.get(timeout=0.1)
                if line is None:
                    stream_closed = True
                else:
                    emit_process_line(line)
            except queue.Empty:
                pass
            if process.poll() is not None and stream_closed:
                return int(process.returncode or 0)

    def _run_build(self, job_id: str) -> None:
        job = self.jobs[job_id]
        record = self.store.load(job.project_id)
        root = self.store.project_dir(job.project_id)
        config = self.config_provider()
        self._set(job_id, state="running", started=time.time(), phase="Preparing guided build", progress=3)
        self._log(job_id, f"Guided build started for {record.options.display_name}.")
        try:
            validation = record.to_dict()["guide_validation"]
            if not record.guide.get("locked") or validation.get("errors"):
                raise RuntimeError("The landmark guide is not locked and valid.")

            for name in ("build", "generated", "addon", "reports"):
                path = root / name
                if path.exists():
                    shutil.rmtree(path)
            build_dir = root / "build"
            build_dir.mkdir(parents=True)
            payload = {
                "project_id": record.id,
                "runtime_token": job_id,
                "project_root": str(root),
                "source_glb": str(root / record.source_glb),
                "options": record.options.__dict__,
                "guide": record.guide,
                "toolchain": config.to_dict(),
                "app_root": str(self.app_root),
            }
            payload_path = build_dir / "pipeline_config.json"
            payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            blender_ready = bool(config.blender and Path(config.blender).is_file())
            if not blender_ready:
                self._generate_unprocessed_pack(job_id, root)
                terminal_state, terminal_phase = "blocked", "Blender required"
                textures_compiled = compiled = False
            else:
                self._set(job_id, phase="Generating guided ValveBiped source", progress=10)
                command = [
                    config.blender,
                    "--background",
                    "--factory-startup",
                    "--python-exit-code",
                    "1",
                    "--python",
                    str(self.app_root / "blender" / "pipeline.py"),
                    "--",
                    str(payload_path),
                ]
                code = self._run_command(job_id, command, root)
                if code != 0:
                    raise RuntimeError(f"Blender guided pipeline exited with code {code}.")

                self._set(job_id, phase="Writing deterministic Source VTF textures", progress=64)
                textures_compiled = self._compile_textures(job_id, root, config, record.options.slug)
                if not textures_compiled:
                    terminal_state, terminal_phase = "texture_blocked", "Internal VTF generation failed"
                    compiled = False
                else:
                    self._set(job_id, phase="Compiling Source player model", progress=76)
                    compiled = False
                    if record.options.compile_model:
                        compiled = self._compile_source(job_id, root, config)
                    if record.options.compile_model and not compiled:
                        terminal_state, terminal_phase = "compile_blocked", "StudioMDL required or failed"
                    else:
                        terminal_state, terminal_phase = "built_unverified", "Built, install and validate in Garry's Mod"

            install_result: dict[str, Any] | None = None
            if record.options.package_gma and compiled and textures_compiled:
                self._set(job_id, phase="Packaging addon", progress=87)
                self._package_gma(job_id, root, config, record.options.slug)

            if compiled and textures_compiled:
                self._set(job_id, phase="Installing verified addon into Garry's Mod", progress=93)
                game_dir = Path(config.game_dir).expanduser() if config.game_dir else None
                if not game_dir or not (game_dir / "gameinfo.txt").is_file():
                    terminal_state = "install_blocked"
                    terminal_phase = "Build passed, Garry's Mod installation path is unavailable"
                    self._log(job_id, "Automatic installation was blocked because the configured game directory is invalid.")
                else:
                    try:
                        install_result = install_to_gmod(
                            addon_source=root / "addon",
                            game_dir=game_dir,
                            slug=record.options.slug,
                            display_name=record.options.display_name,
                            project_id=record.id,
                            build_token=job_id,
                        )
                        terminal_state = "installed_unverified"
                        terminal_phase = "Built and installed, restart Garry's Mod for automatic runtime proof"
                        self._log(job_id, f"Installed and verified {install_result['direct_file_count']} direct runtime files plus the managed addon.")
                    except Exception as exc:
                        terminal_state = "install_blocked"
                        terminal_phase = "Build passed, installation verification failed"
                        self._log(job_id, f"Automatic installation failed: {exc}")

            record.state = terminal_state
            self.store.save(record)
            self._post_validate(job_id, root, record, compiled, terminal_state, textures_compiled, install_result)
            artifact = self.store.package_zip(record.id)
            self._set(
                job_id,
                state=terminal_state,
                phase=terminal_phase,
                progress=100,
                finished=time.time(),
                artifact=str(artifact.relative_to(root)).replace("\\", "/"),
            )
            self._log(job_id, f"Build output packaged: {artifact.name}")
            if terminal_state == "installed_unverified":
                self._log(job_id, "Build and installation passed. Restart Garry's Mod and enter Sandbox; the runtime report is written automatically.")
        except Exception as exc:
            self._log(job_id, traceback.format_exc())
            record.state = "failed"
            self.store.save(record)
            self._set(job_id, state="failed", phase="Failed", finished=time.time(), error=str(exc))

    def _generate_unprocessed_pack(self, job_id: str, root: Path) -> None:
        for directory in (root / "generated", root / "addon", root / "reports"):
            directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.app_root / "blender" / "pipeline.py", root / "generated" / "run_blender_pipeline.py")
        (root / "generated" / "BLENDER_REQUIRED.txt").write_text(
            "Install Blender 4.2 or newer, configure blender.exe in Toolchain, then rebuild.\n"
            "The original GLB and locked landmark guide remain in this project.\n",
            encoding="utf-8",
        )
        (root / "reports" / "build_report.json").write_text(json.dumps({
            "status": "blocked",
            "reason": "Blender executable is unavailable",
            "source_files_generated": False,
        }, indent=2), encoding="utf-8")
        self._log(job_id, "Blender is unavailable. No fake model files were generated.")

    @staticmethod
    def _valid_vtf(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size >= 80 and path.read_bytes()[:4] == b"VTF\x00"
        except OSError:
            return False

    @staticmethod
    def _inspect_vtex_tga(path: Path) -> tuple[bool, str]:
        try:
            data = path.read_bytes()
        except OSError as exc:
            return False, f"cannot read source TGA: {exc}"
        if len(data) < 18:
            return False, "header is truncated"
        image_type = data[2]
        width = int.from_bytes(data[12:14], "little")
        height = int.from_bytes(data[14:16], "little")
        depth = data[16]
        descriptor = data[17]
        if image_type != 2:
            return False, f"image type is {image_type}, expected uncompressed true colour type 2"
        if depth not in {24, 32}:
            return False, f"pixel depth is {depth}, expected 24 or 32"
        if width < 4 or height < 4 or width > 2048 or height > 2048:
            return False, f"dimensions are {width}x{height}, expected 4 to 2048"
        if width & (width - 1) or height & (height - 1):
            return False, f"dimensions are {width}x{height}, both axes must be powers of two"
        expected = 18 + width * height * (depth // 8)
        if len(data) != expected:
            return False, f"file size is {len(data)} bytes, expected {expected} for an uncompressed TGA"
        origin = "top" if descriptor & 0x20 else "bottom"
        return True, f"{width}x{height}, {depth} bit, uncompressed, {origin} origin"

    def _compile_textures(self, job_id: str, root: Path, config: ToolchainConfig, slug: str) -> bool:
        source_dir = root / "generated" / "materialsrc" / "models" / "player" / slug
        tga_files = sorted(source_dir.glob("*.tga")) if source_dir.is_dir() else []
        if not tga_files:
            self._log(job_id, "No TGA source textures were generated.")
            return False

        addon_output = root / "addon" / "materials" / "models" / "player" / slug
        addon_output.mkdir(parents=True, exist_ok=True)
        for stale in addon_output.glob("*.vtf"):
            stale.unlink(missing_ok=True)

        game_output: Path | None = None
        if config.game_dir and (Path(config.game_dir) / "gameinfo.txt").is_file():
            game_output = Path(config.game_dir) / "materials" / "models" / "player" / slug
            game_output.mkdir(parents=True, exist_ok=True)

        for source in source_dir.glob("*.vmt"):
            shutil.copy2(source, addon_output / source.name)
            if game_output is not None:
                shutil.copy2(source, game_output / source.name)

        all_valid = True
        compiled_names: list[str] = []
        for source in tga_files:
            destination = addon_output / f"{source.stem}.vtf"
            destination.unlink(missing_ok=True)
            normal_map = source.stem.lower().endswith("_normal")
            try:
                tga_valid, tga_detail = self._inspect_vtex_tga(source)
                self._log(job_id, f"TGA preflight for {source.name}: {tga_detail}.")
                if not tga_valid:
                    all_valid = False
                    continue
                info = write_vtf_from_tga(source, destination, normal_map=normal_map)
                inspect_vtf(destination)
                if game_output is not None:
                    shutil.copy2(destination, game_output / destination.name)
                compiled_names.append(destination.name)
                kind = "normal" if normal_map else "colour"
                self._log(
                    job_id,
                    f"Wrote validated {kind} VTF {destination.name}: "
                    f"{info.width}x{info.height}, BGRA8888, one mip, {destination.stat().st_size} bytes.",
                )
            except Exception as exc:
                destination.unlink(missing_ok=True)
                self._log(job_id, f"VTF generation failed for {source.name}: {exc}")
                all_valid = False

        expected_names = {f"{path.stem}.vtf" for path in tga_files}
        actual_names = set()
        for path in addon_output.glob("*.vtf"):
            try:
                inspect_vtf(path)
            except Exception as exc:
                self._log(job_id, f"Rejected invalid VTF {path.name}: {exc}")
                continue
            actual_names.add(path.name)
        missing = sorted(expected_names - actual_names)
        if missing:
            self._log(job_id, "Validated VTF outputs are missing: " + ", ".join(missing))
            all_valid = False
        self._log(job_id, f"Internal VTF writer produced {len(actual_names)} validated VTF files.")
        return all_valid and actual_names == expected_names

    def _compile_source(self, job_id: str, root: Path, config: ToolchainConfig) -> bool:
        qc_files = list((root / "generated" / "modelsrc").glob("*.qc"))
        if not qc_files:
            self._log(job_id, "No QC file was generated.")
            return False
        if not config.studiomdl or not Path(config.studiomdl).is_file():
            self._log(job_id, "StudioMDL is not configured.")
            return False
        if not config.game_dir or not (Path(config.game_dir) / "gameinfo.txt").is_file():
            self._log(job_id, "The Garry's Mod game directory is not configured for StudioMDL.")
            return False

        qc = qc_files[0]
        slug = qc.stem
        compiled_source = Path(config.game_dir) / "models" / "player" / slug
        addon_models = root / "addon" / "models" / "player" / slug
        # Remove every previous compiler output before invoking StudioMDL. Without
        # this, an old valid file can make a failed rebuild look successful.
        for directory in (compiled_source, addon_models):
            directory.mkdir(parents=True, exist_ok=True)
            for extension in (".mdl", ".vvd", ".dx80.vtx", ".dx90.vtx", ".sw.vtx", ".phy"):
                (directory / f"{slug}{extension}").unlink(missing_ok=True)
        compile_started = time.time()
        code = self._run_command(job_id, [config.studiomdl, "-game", config.game_dir, str(qc)], qc.parent)
        if code != 0:
            self._log(job_id, f"StudioMDL failed with exit code {code}.")
            return False

        missing: list[str] = []
        for extension in (".mdl", ".vvd", ".dx90.vtx", ".phy"):
            source = compiled_source / f"{slug}{extension}"
            if not source.is_file():
                missing.append(source.name)
                continue
            if source.stat().st_mtime + 2 < compile_started:
                missing.append(source.name + " (stale timestamp)")
                continue
            shutil.copy2(source, addon_models / source.name)
        if missing:
            self._log(job_id, "StudioMDL output is incomplete: " + ", ".join(missing))
            return False
        mdl = addon_models / f"{slug}.mdl"
        if mdl.stat().st_size < 1024 or mdl.read_bytes()[:4] not in {b"IDST", b"IDAG"}:
            self._log(job_id, "StudioMDL produced an invalid MDL header or an unexpectedly small file.")
            return False
        self._log(job_id, "StudioMDL output contains MDL, VVD, DX90.VTX and PHY.")
        return True

    def _package_gma(self, job_id: str, root: Path, config: ToolchainConfig, slug: str) -> None:
        if not config.gmad or not Path(config.gmad).is_file():
            self._log(job_id, "GMad packaging was requested, but gmad.exe is unavailable.")
            return
        output = root / "artifacts" / f"{slug}.gma"
        output.parent.mkdir(exist_ok=True)
        code = self._run_command(job_id, [config.gmad, "create", "-folder", str(root / "addon"), "-out", str(output)], root / "addon")
        if code == 0 and output.is_file():
            self._log(job_id, f"GMad package created: {output.name}")
        else:
            self._log(job_id, f"GMad did not create a package. Exit code: {code}.")

    def _post_validate(
        self,
        job_id: str,
        root: Path,
        record,
        compiled: bool,
        terminal_state: str,
        textures_compiled: bool = False,
        install_result: dict[str, Any] | None = None,
    ) -> None:
        reports = root / "reports"
        reports.mkdir(exist_ok=True)
        report_path = reports / "build_report.json"
        try:
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            report = {}

        addon = root / "addon"
        modelsrc = root / "generated" / "modelsrc"
        slug = record.options.slug
        if addon.exists():
            ensure_runtime_addon_files(
                addon,
                slug,
                record.options.display_name,
                project_id=record.id,
                build_token=job_id,
            )

        model_dir = addon / "models" / "player" / slug
        expected_model_files = [model_dir / f"{slug}{extension}" for extension in (".mdl", ".vvd", ".dx90.vtx", ".phy")]
        compiled_files = [str(path.relative_to(root)).replace("\\", "/") for path in expected_model_files if path.is_file()]

        material_dir = addon / "materials" / "models" / "player" / slug
        vmt_files = sorted(material_dir.glob("*.vmt")) if material_dir.is_dir() else []
        vtf_files = sorted(material_dir.glob("*.vtf")) if material_dir.is_dir() else []
        valid_vtf = {path.stem for path in vtf_files if self._valid_vtf(path)}
        required_vtf: set[str] = set()
        for vmt in vmt_files:
            text = vmt.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r'\$(?:basetexture|bumpmap)"?\s+"[^"]*/([^/"]+)"', text, flags=re.IGNORECASE):
                required_vtf.add(match.group(1))
        missing_vtf = sorted(required_vtf - valid_vtf)

        qc = modelsrc / f"{slug}.qc"
        qc_text = qc.read_text(encoding="utf-8", errors="replace") if qc.is_file() else ""
        qci_names = ("ragdoll.qci", "hitbox.qci", "standardikchains.qci")
        qci_exists = {name: (modelsrc / name).is_file() for name in qci_names}
        qci_text = {name: (modelsrc / name).read_text(encoding="utf-8", errors="replace") if qci_exists[name] else "" for name in qci_names}
        reference_smd = modelsrc / f"{slug}_reference.smd"
        smd_text = reference_smd.read_text(encoding="utf-8", errors="replace") if reference_smd.is_file() else ""
        root_match = re.search(r'^0\s+"([^"]+)"\s+-1$', smd_text, flags=re.MULTILINE)
        root_bone = root_match.group(1) if root_match else ""

        registration = addon / "lua" / "autorun" / f"ember_{slug}.lua"
        client_registration = addon / "lua" / "autorun" / "client" / f"00_ember_{slug}_player.lua"
        validation_lua = registration
        entity = addon / "lua" / "entities" / f"ember_{slug}_ragdoll" / "shared.lua"
        registration_text = registration.read_text(encoding="utf-8", errors="replace") if registration.is_file() else ""
        client_registration_text = client_registration.read_text(encoding="utf-8", errors="replace") if client_registration.is_file() else ""
        validation_text = validation_lua.read_text(encoding="utf-8", errors="replace") if validation_lua.is_file() else ""
        entity_text = entity.read_text(encoding="utf-8", errors="replace") if entity.is_file() else ""
        source_checks = report.get("checks") if isinstance(report.get("checks"), dict) else {}
        texture_bake = report.get("texture_bake") if isinstance(report.get("texture_bake"), dict) else {}
        bake_checks = texture_bake.get("checks") if isinstance(texture_bake.get("checks"), dict) else {}
        proofs = texture_bake.get("material_proofs") if isinstance(texture_bake.get("material_proofs"), dict) else {}
        generated_dir = root / "generated"
        rendered_proofs = sorted(
            name for name in (proofs.get("front"), proofs.get("back"))
            if name and (generated_dir / str(name)).is_file()
        )
        unsafe_material_tokens: list[str] = []
        for vmt in vmt_files:
            lower = vmt.read_text(encoding="utf-8", errors="replace").lower()
            for token in ("$phong", "$bumpmap", "$envmap"):
                if token in lower:
                    unsafe_material_tokens.append(f"{vmt.name}:{token}")

        checks = {
            "terminal_state": terminal_state,
            "guide_locked": bool(record.guide.get("locked")),
            "guide_validation_ready": record.to_dict()["guide_validation"]["status"] == "ready",
            "blender_source_created": (root / "generated" / f"{slug}_source.blend").is_file(),
            "reference_smd_created": reference_smd.is_file(),
            "physics_smd_created": (modelsrc / f"{slug}_physics.smd").is_file(),
            "valvebiped_root_is_pelvis": root_bone == "ValveBiped.Bip01_Pelvis",
            "no_fake_valvebiped_root": '"ValveBiped.Bip01"' not in smd_text,
            "exact_stock_smd_bind": bool(source_checks.get("exact_stock_smd_bind")),
            "stock_skeleton_conformance_passed": bool(source_checks.get("stock_skeleton_conformance_passed")),
            "guide_anatomy_valid": bool(source_checks.get("guide_anatomy_valid")),
            "source_axis_contract": bool(source_checks.get("source_axis_contract")),
            "no_definebone_override": bool(source_checks.get("no_definebone_override")) and "$definebone" not in qc_text.lower(),
            "qci_files_created": qci_exists,
            "qc_includes_all_qci": all(f'$include "{name}"' in qc_text for name in qci_names),
            "qc_has_animation_model": '$includemodel "m_anm.mdl"' in qc_text or '$includemodel "f_anm.mdl"' in qc_text,
            "ragdoll_declared": "$collisionjoints" in qci_text["ragdoll.qci"],
            "hitboxes_declared": "$hbox" in qci_text["hitbox.qci"],
            "ik_chains_declared": "$ikchain" in qci_text["standardikchains.qci"],
            "uv_atlas_rebuilt_on_reduced_mesh": bool(source_checks.get("uv_atlas_rebuilt_on_reduced_mesh")),
            "original_uv_map_discarded": bool(source_checks.get("original_uv_map_discarded")),
            "texture_baked_from_high_resolution": bool(source_checks.get("texture_baked_from_high_resolution")),
            "uv_coordinates_finite": bool(bake_checks.get("uv_coordinates_finite")),
            "uv_inside_atlas": bool(bake_checks.get("uv_inside_atlas")),
            "atlas_islands_do_not_overlap": bool(bake_checks.get("islands_do_not_overlap")),
            "every_triangle_has_bake_coverage": bool(bake_checks.get("every_triangle_has_bake_coverage")),
            "atlas_resolution_adequate": bool(bake_checks.get("atlas_resolution_adequate")),
            "texture_bake_failures": list(texture_bake.get("failures") or []),
            "bake_has_colour_variation": bool(bake_checks.get("bake_has_colour_variation")),
            "no_large_unpainted_regions": bool(bake_checks.get("no_large_unpainted_regions")),
            "baked_material_rendered_in_blender": bool(bake_checks.get("baked_material_rendered_in_blender")),
            "baked_material_proofs": rendered_proofs,
            "texture_bake_passed": bool(texture_bake.get("passed")),
            "lod_atlas_islands_do_not_overlap": bool(source_checks.get("lod_atlas_islands_do_not_overlap")),
            "vtf_written_and_validated": textures_compiled,
            "vtex_compiled": textures_compiled,  # retained for V2 UI and report compatibility
            "valid_vtf_count": len(valid_vtf),
            "all_vtf_headers_valid": bool(vtf_files) and len(valid_vtf) == len(vtf_files),
            "material_references_complete": bool(vmt_files) and not missing_vtf,
            "missing_vtf_references": missing_vtf,
            "safe_base_only_materials": not unsafe_material_tokens,
            "unsafe_material_tokens": unsafe_material_tokens,
            "studiomdl_compiled": compiled,
            "compiled_files": compiled_files,
            "runtime_registration_created": registration.is_file(),
            "dedicated_client_registration_created": client_registration.is_file(),
            "player_manager_registration_created": "player_manager.AddValidModel(ID, MODEL)" in registration_text and "player_manager.AddValidModel(ID, MODEL)" in client_registration_text,
            "player_selector_registration_created": 'list.Set("PlayerOptionsModel", DISPLAY, MODEL)' in registration_text and 'list.Set("PlayerOptionsModel", ID, MODEL)' in client_registration_text,
            "registration_retry_created": 'timer.Simple(5, registerModel)' in registration_text,
            "auto_install_verified": bool(install_result and install_result.get("installed")),
            "installed_direct_file_count": int(install_result.get("direct_file_count", 0)) if install_result else 0,
            "spawnable_ragdoll_created": "ENT.Spawnable = true" in entity_text and 'ents.Create("prop_ragdoll")' in entity_text,
            "runtime_animation_validation_created": "activities_valid" in validation_text and "material_error_count" in validation_text,
            "runtime_failure_sentinel_created": "installed_waiting_for_game" in (Path(install_result["manifest"]).with_name(f"runtime_{slug}.json").read_text(encoding="utf-8", errors="replace") if install_result else ""),
            "runtime_check_required_for_complete": True,
            "gma_created": (root / "artifacts" / f"{slug}.gma").is_file(),
        }
        report["post_build"] = checks
        if install_result:
            report["installation"] = install_result
        report["status"] = terminal_state
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        lines = [
            f"# {record.options.display_name} validation",
            "",
            f"Current state: `{terminal_state}`",
            "",
            "The project is not Complete until Garry's Mod confirms the model, materials, ValveBiped bones, player activities, selector registration and ragdoll entity.",
            "",
            "## Automatic build checks",
            "",
        ]
        for key, value in checks.items():
            lines.append(f"* {key}: `{json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value}`")
        lines.extend([
            "",
            "## Required in game check",
            "",
            f"Install the project, restart Garry's Mod, enter Sandbox, then run `ember_validate_{slug}`.",
            "Return to the Builder and select Read GMod Runtime Check.",
        ])
        (reports / "FINAL_VALIDATION.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        self._log(job_id, "Strict post build validation report written.")
