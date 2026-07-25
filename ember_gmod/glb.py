from __future__ import annotations

import json
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

GLB_MAGIC = b"glTF"
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942


class GLBError(ValueError):
    pass


@dataclass
class GLBInspection:
    filename: str
    bytes: int
    glb_version: int
    generator: str | None
    scenes: int
    nodes: int
    meshes: int
    primitives: int
    vertices: int
    triangles: int
    materials: int
    textures: int
    images: int
    skins: int
    animations: int
    cameras: int
    lights: int
    bounding_box: dict[str, list[float]] | None
    material_names: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_chunks(data: bytes) -> tuple[dict[str, Any], list[bytes], int]:
    if len(data) < 12:
        raise GLBError("File is too small to be a GLB container.")
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC:
        raise GLBError("File does not begin with the GLB magic value 'glTF'.")
    if length > len(data):
        raise GLBError("GLB header length is larger than the uploaded file.")
    offset = 12
    doc: dict[str, Any] | None = None
    bins: list[bytes] = []
    while offset + 8 <= length:
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        offset += 8
        end = offset + chunk_length
        if end > length:
            raise GLBError("A GLB chunk extends beyond the declared file length.")
        payload = data[offset:end]
        offset = end
        if chunk_type == JSON_CHUNK:
            try:
                doc = json.loads(payload.rstrip(b"\x00 \t\r\n").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GLBError(f"GLB JSON chunk is invalid: {exc}") from exc
        elif chunk_type == BIN_CHUNK:
            bins.append(payload)
    if doc is None:
        raise GLBError("GLB does not contain a JSON chunk.")
    return doc, bins, version


def inspect_glb(path: str | Path) -> GLBInspection:
    p = Path(path)
    data = p.read_bytes()
    doc, _bins, version = _read_chunks(data)

    accessors = doc.get("accessors", [])
    meshes = doc.get("meshes", [])
    materials = doc.get("materials", [])
    warnings: list[str] = []
    primitive_count = 0
    vertex_count = 0
    triangle_count = 0
    bounds_min = [float("inf"), float("inf"), float("inf")]
    bounds_max = [float("-inf"), float("-inf"), float("-inf")]
    has_bounds = False

    for mesh in meshes:
        for primitive in mesh.get("primitives", []):
            primitive_count += 1
            attrs = primitive.get("attributes", {})
            pos_index = attrs.get("POSITION")
            if isinstance(pos_index, int) and 0 <= pos_index < len(accessors):
                pos_accessor = accessors[pos_index]
                count = int(pos_accessor.get("count", 0) or 0)
                vertex_count += count
                amin = pos_accessor.get("min")
                amax = pos_accessor.get("max")
                if isinstance(amin, list) and isinstance(amax, list) and len(amin) >= 3 and len(amax) >= 3:
                    has_bounds = True
                    for i in range(3):
                        bounds_min[i] = min(bounds_min[i], float(amin[i]))
                        bounds_max[i] = max(bounds_max[i], float(amax[i]))
            mode = int(primitive.get("mode", 4))
            index_accessor = primitive.get("indices")
            element_count = 0
            if isinstance(index_accessor, int) and 0 <= index_accessor < len(accessors):
                element_count = int(accessors[index_accessor].get("count", 0) or 0)
            elif isinstance(pos_index, int) and 0 <= pos_index < len(accessors):
                element_count = int(accessors[pos_index].get("count", 0) or 0)
            if mode == 4:  # TRIANGLES
                triangle_count += element_count // 3
            elif mode in (5, 6):  # TRIANGLE_STRIP / TRIANGLE_FAN
                triangle_count += max(0, element_count - 2)
            else:
                warnings.append(f"Primitive mode {mode} is not a triangle mode and may require conversion.")

    skins = doc.get("skins", [])
    animations = doc.get("animations", [])
    if not skins:
        warnings.append("No skin data found. Character rigging is required.")
    if not animations:
        warnings.append("No animation data found. Garry's Mod animations must be included during compile.")
    if triangle_count > 100_000:
        warnings.append("High triangle count detected. LOD generation and decimation are strongly recommended.")
    if not materials:
        warnings.append("No material records found. A fallback Source material will be generated.")

    extensions = doc.get("extensions", {})
    lights = 0
    if isinstance(extensions, dict):
        khr_lights = extensions.get("KHR_lights_punctual", {})
        if isinstance(khr_lights, dict):
            lights = len(khr_lights.get("lights", []) or [])

    asset = doc.get("asset", {}) if isinstance(doc.get("asset"), dict) else {}
    generator = asset.get("generator")
    bbox = None
    if has_bounds:
        bbox = {
            "min": [round(v, 6) for v in bounds_min],
            "max": [round(v, 6) for v in bounds_max],
            "size": [round(bounds_max[i] - bounds_min[i], 6) for i in range(3)],
        }

    names = []
    for i, material in enumerate(materials):
        raw = str(material.get("name") or f"material_{i + 1}")
        names.append(raw[:80])

    return GLBInspection(
        filename=p.name,
        bytes=len(data),
        glb_version=version,
        generator=str(generator) if generator else None,
        scenes=len(doc.get("scenes", []) or []),
        nodes=len(doc.get("nodes", []) or []),
        meshes=len(meshes),
        primitives=primitive_count,
        vertices=vertex_count,
        triangles=triangle_count,
        materials=len(materials),
        textures=len(doc.get("textures", []) or []),
        images=len(doc.get("images", []) or []),
        skins=len(skins),
        animations=len(animations),
        cameras=len(doc.get("cameras", []) or []),
        lights=lights,
        bounding_box=bbox,
        material_names=names,
        warnings=warnings,
    )


def validate_glb_bytes(data: bytes) -> None:
    _read_chunks(data)
