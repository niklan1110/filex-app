"""App-shell headers on the UI (task 60, review C2 fix 2, ADR-0009 "Browser concerns")."""

import re
from pathlib import Path

import pytest

from filex.api.deps import Settings

DIST = Path(__file__).parents[2] / "web" / "dist"
KEEL = "https://Keel.Example.com:8443/filex"


@pytest.fixture
def web(tmp_path):
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><div id=root></div>")
    (root / "assets" / "app.js").write_text("export {}")
    return root


def csp(res) -> dict[str, str]:
    parts = (p.strip().split(" ", 1) for p in res.headers["content-security-policy"].split(";"))
    return {p[0]: p[1] if len(p) > 1 else "" for p in parts}


def test_shell_csp_names_only_the_keel_origin(make_client, web):
    client = make_client(auth="", keel_url=KEEL, keel_anon_key="k", web_dir=web)
    for path in ("/", "/tools/pdf.merge", "/assets/app.js"):
        res = client.get(path)
        assert res.status_code == 200
        policy = csp(res)
        assert policy["connect-src"] == "'self' https://keel.example.com:8443"  # no path
        assert policy["default-src"] == policy["script-src"] == policy["style-src"] == "'self'"
        assert policy["frame-ancestors"] == policy["object-src"] == policy["base-uri"] == "'none'"
        urls = re.findall(r"https?://[^;\s]+", res.headers["content-security-policy"])
        assert urls == ["https://keel.example.com:8443"]
        assert "unsafe" not in res.headers["content-security-policy"]
        assert res.headers["referrer-policy"] == "no-referrer"
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert "camera=()" in res.headers["permissions-policy"]


def test_without_keel_connect_src_is_self_only(make_client, web):
    assert csp(make_client(web_dir=web).get("/"))["connect-src"] == "'self'"


def test_hsts_only_when_enabled(make_client, web):
    assert "strict-transport-security" not in make_client(web_dir=web).get("/").headers
    res = make_client(web_dir=web, hsts=True).get("/")
    assert res.headers["strict-transport-security"].startswith("max-age=")


def test_hsts_setting_reads_filex_hsts(monkeypatch):
    monkeypatch.setenv("FILEX_HSTS", "true")
    assert Settings.from_env().hsts is True
    monkeypatch.setenv("FILEX_HSTS", "false")
    assert Settings.from_env().hsts is False


def test_api_responses_do_not_get_the_shell_csp(make_client, web):
    res = make_client(web_dir=web).get("/api/v1/health")
    assert "content-security-policy" not in res.headers


@pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="web/dist is not built")
def test_built_ui_has_no_inline_script_or_style():
    """``script-src 'self'`` and ``style-src 'self'`` without 'unsafe-inline' must be enough."""
    html = (DIST / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.DOTALL | re.IGNORECASE)
    assert scripts and all("src=" in attrs and not body.strip() for attrs, body in scripts)
    assert not re.search(r"<style\b|\sstyle=|\son[a-z]+=", html, re.IGNORECASE)
    for js in DIST.glob("assets/*.js"):
        text = js.read_text(encoding="utf-8")
        assert "eval(" not in text and "new Function" not in text
