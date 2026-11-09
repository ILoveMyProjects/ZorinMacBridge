from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "https://github.com/ILoveMyProjects/ZorinMacBridge"


def test_project_license_is_no_sale_and_requires_source_attribution():
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "ZorinMacBridge No-Sale Source Attribution License 1.0" in license_text
    assert "NO SALE OR PAID REDISTRIBUTION" in license_text
    assert REPO in license_text
    assert REPO in notice
    assert REPO in readme
    assert "MIT License" not in license_text


def test_release_packages_embed_license_and_notice():
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert 'install -m 0644 LICENSE NOTICE "$PKG/usr/share/doc/zorinmacbridge/"' in workflow
    assert "--add-data 'LICENSE:.'" in workflow
    assert "--add-data 'NOTICE:.'" in workflow
    assert "cp LICENSE NOTICE build/dmgroot/" in workflow
