"""The attachment headers on every route that serves output bytes (task 59, review C2 fix 1,
M5, review 5.1 condition 4)."""

import json
from urllib.parse import quote

from filex.jobs import JobPaths, job_key

PDF = b"%PDF-1.7\n%headers\n"
HTML = b"<html><script>alert(1)</script></html>"
NAME = 'résumé "q"\r\nX-Evil: 1/a.pdf'


def html_job(client, r, data) -> str:
    res = client.post("/api/v1/tools/api.pdf/jobs", files=[("files", ("a.pdf", PDF, "x/y"))])
    job_id = res.json()["id"]
    out = JobPaths(data, job_id).output(0, "html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(HTML)
    r.hset(
        job_key(job_id),
        mapping={
            "status": "SUCCEEDED",
            "inputs": json.dumps([{"format": "pdf", "name": NAME, "size": len(PDF)}]),
            "outputs": json.dumps(
                [{"index": 0, "format": "html", "size": len(HTML), "role": "RESULT"}]
            ),
        },
    )
    return job_id


def test_all_three_output_routes_send_the_same_attachment_headers(client, r, data):
    job_id = html_job(client, r, data)
    link = client.post(f"/api/v1/jobs/{job_id}/outputs/0/link").json()["url"]
    responses = [
        client.get(f"/api/v1/jobs/{job_id}/result"),
        client.get(f"/api/v1/jobs/{job_id}/outputs/0"),
        client.get(link),
    ]
    for res in responses:
        assert res.status_code == 200 and res.content == HTML
        assert res.headers["content-type"] == "application/octet-stream"  # never text/html
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["content-security-policy"] == "sandbox; default-src 'none'"
        assert res.headers["cache-control"] == "no-store"
        assert res.headers["content-disposition"].startswith("attachment; ")
    assert len({res.headers["content-disposition"] for res in responses}) == 1


def test_filename_is_sanitised_with_a_utf8_filename_star(client, r, data):
    job_id = html_job(client, r, data)
    cd = client.get(f"/api/v1/jobs/{job_id}/result").headers["content-disposition"]
    ascii_part, star = cd.removeprefix("attachment; ").split("; ")
    fallback = ascii_part.removeprefix('filename="').removesuffix('"')
    assert fallback == "resume qX-Evil 1a.html"  # ":" dropped (review L-6)
    assert not any(c in fallback for c in '"\\/\r\n')
    expected = "résumé qX-Evil 1a.html"
    assert star == "filename*=UTF-8''" + quote(expected, safe="")


def test_job_json_is_not_cached(client, r, data):
    job_id = html_job(client, r, data)
    assert client.get(f"/api/v1/jobs/{job_id}").headers["cache-control"] == "no-store"
    assert client.get("/api/v1/jobs").headers["cache-control"] == "no-store"
