"""Deployment boundaries (DESIGN §14): the public image and proxy config must not
expose private data, extra ports or API docs. Plain text checks, no Docker needed."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _copies():
    lines = (DEPLOY / "api.Dockerfile").read_text(encoding="utf-8").splitlines()
    return [ln.split()[1] for ln in lines if ln.startswith("COPY ")]


def test_image_copies_only_public_code_and_demo_fixtures():
    sources = _copies()
    assert sources, "no COPY lines found"
    for src in sources:
        assert not re.search(r"private|\.env|v0_draft|data-cache|memo", src, re.I), src
        assert src != "." and not src.endswith("/"), f"broad COPY: {src}"
        assert (ROOT / src).exists(), f"missing: {src}"
    assert "fixtures/rules/demo.yaml" in sources and "backend/prompts" in sources


def test_dockerignore_is_allowlist():
    lines = [ln.strip() for ln in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    assert lines[0] == "*"
    allowed = [ln[1:] for ln in lines if ln.startswith("!")]
    assert not any(re.search(r"private|\.env|data-cache", a) for a in allowed)
    for src in _copies():  # everything the Dockerfile copies must be in the build context
        assert any(src == a or a.startswith(src + "/") or src.startswith(a.rstrip("*").rstrip("/"))
                   for a in allowed), src


def test_runtime_is_non_root_single_worker():
    text = (DEPLOY / "api.Dockerfile").read_text(encoding="utf-8")
    assert "\nUSER app" in text and '"--workers", "1"' in text
    assert "API_DOCS" not in text


def test_requirements_pinned():
    reqs = [ln for ln in (DEPLOY / "requirements-api.txt").read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]
    assert reqs and all("==" in r for r in reqs)


def test_compose_api_has_no_ports_and_no_database_network():
    compose = yaml.safe_load((DEPLOY / "compose.invest.yaml").read_text(encoding="utf-8"))
    api = compose["services"]["investment-api"]
    assert "ports" not in api and "expose" not in api
    assert api["networks"] == ["invest"]
    assert api["read_only"] is True and "ALL" in api["cap_drop"]
    assert api["deploy"]["resources"]["limits"]["memory"] == "256M"
    env = api.get("environment", {})
    # The key only comes from the server's env file, and the skeptic is off unless explicitly enabled.
    assert env["ANTHROPIC_API_KEY"] == "${INVEST_ANTHROPIC_API_KEY:-}"
    assert env["LAB_SKEPTIC_ENABLED"] == "${LAB_SKEPTIC_ENABLED:-0}"
    assert env["LAB_DAILY_BUDGET_USD"] == "${LAB_DAILY_BUDGET_USD:-0.5}"
    assert "invest_lab_data:/data/lab" in api["volumes"]
    assert set(compose["services"]["caddy"]) == {"volumes", "networks"}  # override only adds mounts


def test_caddy_blocks_docs_and_proxies_api():
    text = (DEPLOY / "invest.caddy").read_text(encoding="utf-8")
    assert "invest.{$DOMAIN}" in text
    assert "handle /api/docs*" in text and "handle /api/openapi.json" in text
    assert "reverse_proxy investment-api:8081" in text
    assert "redir * /lab 308" in text  # "redir /lab 308" would treat /lab as a matcher
    assert "Content-Security-Policy" in text and "frame-ancestors 'none'" in text
