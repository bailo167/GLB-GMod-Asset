from pathlib import Path


def test_web_ui_contains_guided_workbench_and_strict_build_contract():
    root = Path(__file__).resolve().parents[1] / "web"
    html = (root / "index.html").read_text(encoding="utf-8")
    app = (root / "app.js").read_text(encoding="utf-8")
    viewer = (root / "viewer.js").read_text(encoding="utf-8")
    assert 'id="selectGlbBtn"' in html
    assert 'id="glCanvas"' in html
    assert 'id="landmarkList"' in html
    assert 'id="lockGuideBtn"' in html
    assert 'id="centerDepthBtn"' in html
    assert 'id="reviewPointsBtn"' in html
    assert "Repair Installation" in html
    assert 'id="runtimeCheckBtn"' in html
    assert 'Build + Install' in html
    assert "REQUIRED_SERVICE_VERSION = '2.2.4'" in app
    assert "validateGlbHeader" in app
    assert "inspectSelectedGlb" in app
    assert "CENTER_DEPTH" in app
    assert "fromSurface&&CENTER_DEPTH.has(id)" in app
    assert "advanceGuidedReview" in app
    assert "checks.vtf_written_and_validated||checks.vtex_compiled" in app
    assert "/runtime-check" in app
    assert 'id="textureProof"' in html
    assert 'id="textureProofBadge"' in html
    assert "BAKED TEXTURE PROOF" in html
    assert "function checkRowState(name,value)" in app
    assert "class GLBViewer" in viewer
    assert "raycast" in viewer
    assert "autoSeed" in viewer
    assert "if (this.frontAxis === 'neg_y') return [-y, x, z]" in viewer
    assert "No remote requests" in html
