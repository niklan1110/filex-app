"""The API serves the built UI; client-side routes fall back to index.html (plan task 9)."""


def test_ui_routes_fall_back_to_index(make_client, tmp_path):
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><div id=root></div>")
    (web / "assets" / "app.js").write_text("export {}")
    client = make_client(web_dir=web)

    for path in ("/", "/tools/pdf.merge"):
        res = client.get(path)
        assert res.status_code == 200
        assert "id=root" in res.text
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "NOT_FOUND"
