import sys
from pathlib import Path

from ember_gmod.jobs import JobManager


def test_vtex_numeric_progress_is_compacted(tmp_path: Path):
    manager = JobManager(tmp_path, None, lambda: None)  # type: ignore[arg-type]
    manager.logs["job"] = []
    code = manager._run_command(
        "job",
        [
            sys.executable,
            "-c",
            "print('100 100 0'); print('100 50 50'); print('100 0 100')",
        ],
        tmp_path,
        timeout=5,
        progress_label="VTEX body.tga",
    )
    assert code == 0
    output_logs = manager.logs["job"][1:]
    joined = "\n".join(output_logs)
    assert not any(line.rstrip().endswith("100 50 50") for line in output_logs)
    assert "VTEX body.tga: 50%" in joined
    assert "VTEX body.tga: 100%" in joined
