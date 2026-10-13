"""Job ids (task 61, ADR-0007 item 5): 128-bit, base64url, checked before any Redis or /data use."""

import pytest

from filex.jobs import JOB_ID, JobPaths, new_job_id, valid_job_id


def test_new_ids_match_the_regex_and_carry_128_bits():
    ids = {new_job_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(JOB_ID.fullmatch(i) and len(i) == 24 and valid_job_id(i) for i in ids)


@pytest.mark.parametrize(
    "job_id",
    ["nope", "j_" + "A" * 21, "j_" + "A" * 23, "j_" + "A" * 21 + "!", "j_" + "A" * 21 + "."],
)
def test_malformed_id_is_404_without_touching_redis_or_data(client, data, job_id):
    def no_redis(*args, **kwargs):
        raise AssertionError(f"Redis was called: {args[:2]}")

    client.app.state.redis.execute_command = no_redis
    client.app.state.redis.pipeline = no_redis
    before = sorted(data.rglob("*")) if data.exists() else []
    for method, path in [
        ("GET", ""),
        ("GET", "/result"),
        ("GET", "/outputs/0"),
        ("DELETE", ""),
        ("POST", "/result/link"),
    ]:
        res = client.request(method, f"/api/v1/jobs/{job_id}{path}")
        assert res.status_code in (204, 404), (method, path, res.status_code)
        if res.status_code == 404:
            assert res.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert (sorted(data.rglob("*")) if data.exists() else []) == before
    with pytest.raises(ValueError):
        JobPaths(data, job_id)
