from __future__ import annotations

import json
import math
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO

from .glb import inspect_glb

SLUG_RE = re.compile(r"[^a-z0-9_]+")

REQUIRED_LANDMARKS = (
    "head_top",
    "neck_base",
    "shoulder_l",
    "elbow_l",
    "wrist_l",
    "shoulder_r",
    "elbow_r",
    "wrist_r",
    "pelvis",
    "hip_l",
    "knee_l",
    "ankle_l",
    "toe_l",
    "hip_r",
    "knee_r",
    "ankle_r",
    "toe_r",
)

OPTIONAL_LANDMARKS = (
    "chin",
    "eye_l",
    "eye_r",
    "hand_tip_l",
    "hand_tip_r",
    "heel_l",
    "heel_r",
)

LANDMARK_IDS = REQUIRED_LANDMARKS + OPTIONAL_LANDMARKS


def slugify(value: str) -> str:
    value = value.strip().lower().replace("-", "_").replace(" ", "_")
    value = SLUG_RE.sub("_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:48] or "character"


def _finite_point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        point = [float(value[0]), float(value[1]), float(value[2])]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in point):
        return None
    return point


def validate_guide(guide: dict[str, Any], target_height: float) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    landmarks_raw = guide.get("landmarks", {})
    landmarks: dict[str, list[float]] = {}
    if not isinstance(landmarks_raw, dict):
        landmarks_raw = {}
        errors.append("Landmark data is not an object.")

    for key, raw in landmarks_raw.items():
        if key not in LANDMARK_IDS:
            continue
        point = _finite_point(raw)
        if point is None:
            errors.append(f"Landmark {key} does not contain three finite coordinates.")
            continue
        landmarks[key] = point

    missing = [key for key in REQUIRED_LANDMARKS if key not in landmarks]
    if missing:
        errors.append("Assign all required landmarks: " + ", ".join(missing))

    limit = max(180.0, float(target_height) * 2.5)
    for key, point in landmarks.items():
        if any(abs(v) > limit for v in point):
            errors.append(f"Landmark {key} is outside the expected character bounds.")

    def z(name: str) -> float | None:
        return landmarks.get(name, [0.0, 0.0, float("nan")])[2] if name in landmarks else None

    if not missing:
        if not (z("head_top") > z("neck_base") > z("pelvis")):
            errors.append("Head, neck and pelvis must descend in that order.")
        for side in ("l", "r"):
            if not (z(f"hip_{side}") > z(f"knee_{side}") > z(f"ankle_{side}")):
                errors.append(f"The {side.upper()} hip, knee and ankle must descend in that order.")
            if z(f"toe_{side}") > z(f"ankle_{side}") + target_height * 0.12:
                warnings.append(f"The {side.upper()} toe is unusually high above the ankle.")

        if landmarks["shoulder_l"][1] <= landmarks["shoulder_r"][1]:
            errors.append("Model left landmarks must be on the +Y side and model right landmarks on the -Y side.")
        if landmarks["hip_l"][1] <= landmarks["hip_r"][1]:
            errors.append("Left and right hip landmarks appear reversed.")

        pairs = (
            ("shoulder_l", "shoulder_r"),
            ("elbow_l", "elbow_r"),
            ("wrist_l", "wrist_r"),
            ("hip_l", "hip_r"),
            ("knee_l", "knee_r"),
            ("ankle_l", "ankle_r"),
            ("toe_l", "toe_r"),
        )
        for left, right in pairs:
            zl = landmarks[left][2]
            zr = landmarks[right][2]
            if abs(zl - zr) > target_height * 0.12:
                warnings.append(f"{left} and {right} differ greatly in height. Confirm the model is level.")

        for a, b, label in (
            ("shoulder_l", "elbow_l", "left upper arm"),
            ("elbow_l", "wrist_l", "left forearm"),
            ("shoulder_r", "elbow_r", "right upper arm"),
            ("elbow_r", "wrist_r", "right forearm"),
            ("hip_l", "knee_l", "left thigh"),
            ("knee_l", "ankle_l", "left calf"),
            ("hip_r", "knee_r", "right thigh"),
            ("knee_r", "ankle_r", "right calf"),
        ):
            pa, pb = landmarks[a], landmarks[b]
            distance = math.dist(pa, pb)
            if distance < target_height * 0.055:
                errors.append(f"The {label} landmark segment is too short.")

    status = "ready" if not errors else "incomplete"
    return {
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "required_count": len(REQUIRED_LANDMARKS),
        "assigned_required": len([k for k in REQUIRED_LANDMARKS if k in landmarks]),
        "assigned_total": len(landmarks),
        "landmarks": landmarks,
    }


@dataclass
class BuildOptions:
    display_name: str
    slug: str
    animation_base: str = "male"
    target_height: float = 72.0
    quality: str = "good"
    texture_size: int = 1024
    generate_hands: bool = False
    compile_model: bool = True
    package_gma: bool = True
    rig_mode: str = "guided"
    front_axis: str = "neg_y"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BuildOptions":
        display_name = str(raw.get("display_name", "Character")).strip()[:80] or "Character"
        slug = slugify(str(raw.get("slug") or display_name))
        animation_base = str(raw.get("animation_base", "male"))
        if animation_base not in {"male", "female"}:
            animation_base = "male"
        quality = str(raw.get("quality", "good"))
        if quality not in {"fast", "good", "workshop"}:
            quality = "good"
        try:
            texture_size = int(raw.get("texture_size", 1024))
        except (TypeError, ValueError):
            texture_size = 1024
        if texture_size not in {512, 1024, 2048}:
            texture_size = 1024
        try:
            target_height = float(raw.get("target_height", 72.0))
        except (TypeError, ValueError):
            target_height = 72.0
        target_height = max(36.0, min(120.0, target_height))
        front_axis = str(raw.get("front_axis", "neg_y"))
        if front_axis not in {"neg_y", "pos_y", "pos_x", "neg_x"}:
            front_axis = "neg_y"
        return cls(
            display_name=display_name,
            slug=slug,
            animation_base=animation_base,
            target_height=target_height,
            quality=quality,
            texture_size=texture_size,
            generate_hands=bool(raw.get("generate_hands", False)),
            compile_model=bool(raw.get("compile_model", True)),
            package_gma=bool(raw.get("package_gma", True)),
            rig_mode="guided",
            front_axis=front_axis,
        )


@dataclass
class ProjectRecord:
    id: str
    created: float
    updated: float
    state: str
    options: BuildOptions
    source_glb: str
    inspection: dict[str, Any]
    guide: dict[str, Any] = field(default_factory=dict)
    reference_smd: str | None = None
    last_job_id: str | None = None
    artifact_zip: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["options"] = asdict(self.options)
        result["guide_validation"] = validate_guide(self.guide, self.options.target_height)
        return result


class ProjectStore:
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.projects_dir = workspace / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def project_dir(self, project_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{12}", project_id):
            raise ValueError("Invalid project id")
        return self.projects_dir / project_id

    def record_path(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "project.json"

    def create(
        self,
        glb_stream: BinaryIO,
        glb_filename: str,
        options: BuildOptions,
        reference_stream: BinaryIO | None = None,
        reference_filename: str | None = None,
    ) -> ProjectRecord:
        project_id = uuid.uuid4().hex[:12]
        root = self.project_dir(project_id)
        source = root / "source"
        source.mkdir(parents=True)
        glb_path = source / "model.glb"
        with glb_path.open("wb") as fh:
            shutil.copyfileobj(glb_stream, fh)
        inspection = inspect_glb(glb_path).to_dict()

        now = time.time()
        guide = {
            "version": 2,
            "locked": False,
            "landmarks": {},
            "rigid_zones": [],
            "notes": "",
        }
        record = ProjectRecord(
            id=project_id,
            created=now,
            updated=now,
            state="guide_required",
            options=options,
            source_glb=str(glb_path.relative_to(root)),
            inspection=inspection,
            guide=guide,
        )
        self.save(record)
        return record

    def save(self, record: ProjectRecord) -> None:
        root = self.project_dir(record.id)
        root.mkdir(parents=True, exist_ok=True)
        record.updated = time.time()
        self.record_path(record.id).write_text(json.dumps(record.to_dict(), indent=2), encoding="utf-8")

    def load(self, project_id: str) -> ProjectRecord:
        raw = json.loads(self.record_path(project_id).read_text(encoding="utf-8"))
        options = BuildOptions.from_dict(raw.get("options", {}))
        guide = raw.get("guide") if isinstance(raw.get("guide"), dict) else {}
        if not guide:
            guide = {"version": 2, "locked": False, "landmarks": {}, "rigid_zones": [], "notes": ""}
        record = ProjectRecord(
            id=str(raw["id"]),
            created=float(raw["created"]),
            updated=float(raw["updated"]),
            state=str(raw.get("state", "guide_required")),
            options=options,
            source_glb=str(raw["source_glb"]),
            inspection=dict(raw.get("inspection", {})),
            guide=guide,
            reference_smd=raw.get("reference_smd"),
            last_job_id=raw.get("last_job_id"),
            artifact_zip=raw.get("artifact_zip"),
        )
        guide_validation = validate_guide(record.guide, record.options.target_height)
        if record.state != "building" and (not record.guide.get("locked") or guide_validation["errors"]):
            record.state = "guide_required"
        return record

    def update_guide(self, project_id: str, raw: dict[str, Any]) -> ProjectRecord:
        record = self.load(project_id)
        landmarks_raw = raw.get("landmarks", {})
        landmarks: dict[str, list[float]] = {}
        if isinstance(landmarks_raw, dict):
            for key, value in landmarks_raw.items():
                if key not in LANDMARK_IDS:
                    continue
                point = _finite_point(value)
                if point is not None:
                    landmarks[key] = point

        rigid_zones: list[dict[str, Any]] = []
        raw_zones = raw.get("rigid_zones", [])
        allowed_bones = {
            "ValveBiped.Bip01_Head1",
            "ValveBiped.Bip01_Spine4",
            "ValveBiped.Bip01_Spine2",
            "ValveBiped.Bip01_Pelvis",
            "ValveBiped.Bip01_L_UpperArm",
            "ValveBiped.Bip01_R_UpperArm",
            "ValveBiped.Bip01_L_Thigh",
            "ValveBiped.Bip01_R_Thigh",
        }
        if isinstance(raw_zones, list):
            for item in raw_zones[:24]:
                if not isinstance(item, dict):
                    continue
                center = _finite_point(item.get("center"))
                bone = str(item.get("bone", ""))
                try:
                    radius = float(item.get("radius", 2.0))
                except (TypeError, ValueError):
                    continue
                if center is None or bone not in allowed_bones or not math.isfinite(radius):
                    continue
                rigid_zones.append({
                    "id": str(item.get("id") or uuid.uuid4().hex[:8]),
                    "center": center,
                    "radius": max(0.25, min(record.options.target_height * 0.35, radius)),
                    "bone": bone,
                    "label": str(item.get("label", "Rigid zone"))[:60],
                })

        guide = {
            "version": 2,
            "locked": bool(raw.get("locked", False)),
            "landmarks": landmarks,
            "rigid_zones": rigid_zones,
            "notes": str(raw.get("notes", ""))[:4000],
        }
        validation = validate_guide(guide, record.options.target_height)
        if guide["locked"] and validation["errors"]:
            guide["locked"] = False
        record.guide = guide
        record.state = "guide_ready" if guide["locked"] and not validation["errors"] else "guide_required"
        self.save(record)
        return record

    def list(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.projects_dir.glob("*/project.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                project_id = str(raw.get("id", path.parent.name))
                records.append(self.load(project_id).to_dict())
            except (OSError, json.JSONDecodeError, ValueError, KeyError):
                continue
        return records

    def delete(self, project_id: str) -> None:
        shutil.rmtree(self.project_dir(project_id))

    def list_files(self, project_id: str) -> list[dict[str, Any]]:
        root = self.project_dir(project_id)
        rows = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                rows.append({
                    "path": str(p.relative_to(root)).replace("\\", "/"),
                    "bytes": p.stat().st_size,
                })
        return rows

    def package_zip(self, project_id: str) -> Path:
        record = self.load(project_id)
        root = self.project_dir(project_id)
        out_dir = root / "artifacts"
        out_dir.mkdir(exist_ok=True)
        zip_path = out_dir / f"{record.options.slug}_gmod_builder_output.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for p in root.rglob("*"):
                if not p.is_file() or p == zip_path:
                    continue
                rel = p.relative_to(root)
                if rel.parts and rel.parts[0] == "artifacts" and p.suffix.lower() == ".zip":
                    continue
                zf.write(p, arcname=str(rel).replace("\\", "/"))
        record.artifact_zip = str(zip_path.relative_to(root))
        self.save(record)
        return zip_path
