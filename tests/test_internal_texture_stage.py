from pathlib import Path

from blender.tga_writer import write_uncompressed_tga
from ember_gmod.config import ToolchainConfig
from ember_gmod.jobs import Job, JobManager
from ember_gmod.vtf_writer import inspect_vtf


class DummyStore:
    pass


def test_texture_stage_does_not_require_vtex_executable(tmp_path: Path):
    slug = "character"
    source = tmp_path / "generated" / "materialsrc" / "models" / "player" / slug
    source.mkdir(parents=True)
    write_uncompressed_tga(
        source / "body.tga",
        4,
        4,
        [0.25, 0.5, 0.75, 1.0] * 16,
        srgb=True,
        include_alpha=False,
    )
    (source / "body.vmt").write_text(
        '"VertexLitGeneric"\n{\n    "$basetexture" "models/player/character/body"\n}\n',
        encoding="utf-8",
    )
    manager = JobManager(tmp_path, DummyStore(), lambda: ToolchainConfig())
    manager.jobs["job"] = Job("job", "project", "running", "textures", 0, 0)
    manager.logs["job"] = []
    assert manager._compile_textures("job", tmp_path, ToolchainConfig(vtex=""), slug)
    output = tmp_path / "addon" / "materials" / "models" / "player" / slug / "body.vtf"
    assert inspect_vtf(output).width == 4
    assert any("Internal VTF writer produced 1 validated VTF files" in line for line in manager.logs["job"])
