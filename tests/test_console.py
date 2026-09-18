"""The console: token on every write, CSP that forbids external origins, no external assets."""
import os
import re

import pytest

from sentinel import config

pytest.importorskip("flask")

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sentinel", "api", "static")


@pytest.fixture(scope="module")
def client():
    from sentinel.api import app as api
    api.app.config["TESTING"] = True
    with api.app.test_client() as c:
        yield c


def test_reads_are_open_and_carry_a_locked_down_csp(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp and "font-src 'self'" in csp
    assert "http" not in csp


@pytest.mark.parametrize("path,body", [
    ("/api/command", {"action": "cb_open"}), ("/api/sim", {"hook": "fault_inject", "value": "S2"}),
    ("/api/reset", {}), ("/api/scenario/close_onto_fault", {}), ("/api/scenario/stop", {}),
])
def test_every_mutating_endpoint_rejects_a_missing_or_wrong_token(client, path, body):
    assert client.post(path, json=body).status_code == 401
    assert client.post(path, json=body, headers={"X-Sentinel-Token": "wrong"}).status_code == 401


def test_the_operator_token_is_accepted(client):
    r = client.post("/api/scenario/stop", json={}, headers={"X-Sentinel-Token": config.CONSOLE_TOKEN})
    assert r.status_code == 200 and r.get_json()["ok"] is True
    r = client.post("/api/scenario/stop?token=" + config.CONSOLE_TOKEN, json={})
    assert r.status_code == 200


def test_token_is_generated_once_and_kept_private():
    assert len(config.CONSOLE_TOKEN) >= 20
    if not os.environ.get("SENTINEL_CONSOLE_TOKEN"):
        assert oct(os.stat(config.CONSOLE_TOKEN_PATH).st_mode & 0o777) == "0o600"


def test_static_assets_reference_no_external_origin():
    offenders = []
    for root, _, files in os.walk(STATIC):
        for name in files:
            if name.endswith((".html", ".css", ".js")):
                text = open(os.path.join(root, name), encoding="utf-8").read()
                for url in re.findall(r"https?://[A-Za-z0-9./_-]+", text):
                    if "www.w3.org/2000/svg" not in url and "localhost" not in url:
                        offenders.append((name, url))
    assert not offenders, offenders


def test_index_has_no_inline_script_so_the_csp_can_stay_strict():
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert os.path.exists(os.path.join(STATIC, "fonts", "geist-400.woff2"))
