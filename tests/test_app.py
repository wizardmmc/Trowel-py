"""Tests for trowel_py.app — SPA fallback + path-traversal guard (slice-027-publish)."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app, _resolve_web_dist


@pytest.fixture
def web_dist(tmp_path: Path, monkeypatch) -> Path:
    """Mock _find_web_dist to return a tmp dir with an index.html so the SPA
    fallback route registers."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr("trowel_py.app._find_web_dist", lambda: dist)
    return dist


@pytest.fixture(autouse=True)
def _isolate_memory_root(tmp_path: Path, monkeypatch) -> None:
    """slice-039: lifespan bootstraps layer-one core.md — route
    resolve_memory_root to tmp so app tests never write ~/.trowel/memory.

    slice-046: also disable the in-app review scheduler so app tests neither
    fire a real review nor spawn cc on startup."""
    import trowel_py.memory.paths as paths
    from trowel_py.memory import review_scheduler

    monkeypatch.setattr(paths, "resolve_memory_root", lambda: tmp_path / "mem")
    monkeypatch.setattr(
        review_scheduler,
        "load_review_config",
        lambda *_a, **_k: review_scheduler.ReviewScheduleConfig(
            review_scheduler.DEFAULT_REVIEW_TIME, False
        ),
    )


def test_spa_fallback_serves_index_for_unknown_path(web_dist):
    """Non-/api/* GET → index.html so a frontend route refresh survives."""
    client = TestClient(create_app())
    resp = client.get("/cc/some-deep-route")
    assert resp.status_code == 200
    assert b"SPA" in resp.content


def test_spa_fallback_blocks_path_traversal(tmp_path: Path, web_dist):
    """Encoded ../ must NOT escape web_dist — return index.html, not the file.

    Regression guard for the path-traversal CRITICAL: without the resolve +
    relative_to check, GET /%2e%2e/secret.txt would read any file the backend
    user can. The fix confines candidates to web_dist.
    """
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    client = TestClient(create_app())
    resp = client.get("/%2e%2e/secret.txt")
    assert resp.status_code == 200
    assert b"TOPSECRET" not in resp.content  # traversal blocked → index


def test_api_miss_returns_404_not_index(web_dist):
    """/api/unknown must 404 (not fall through to the SPA index)."""
    client = TestClient(create_app())
    resp = client.get("/api/no-such-endpoint")
    assert resp.status_code == 404


def test_health_still_responds(web_dist):
    """Sanity: a registered /api route isn't shadowed by the SPA catch-all."""
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# === Cache-Control headers ===
# Bug: _spa_fallback 用 FileResponse 但不设 Cache-Control，浏览器启发式缓存
# 旧 index.html（引用旧 hash JS），build 新版后仍加载旧 bundle，要强制刷新
# 才生效。修复：index.html → no-cache（每次验证），assets/*-<hash> → immutable
# （永久缓存，内容变 hash 变文件名变）。

def test_static_cache_headers_index_html_no_cache(tmp_path: Path):
    """index.html → no-cache：入口文件 build 后会变，必须每次向后端验证。"""
    from trowel_py.app import _static_cache_headers
    root = tmp_path
    idx = root / "index.html"
    idx.write_text("<html>")
    headers = _static_cache_headers(idx, root)
    assert headers["cache-control"] == "no-cache"


def test_static_cache_headers_hashed_asset_immutable(tmp_path: Path):
    """assets/*-<hash>.js → immutable + 1 年：Vite 内容变即改 hash 改文件名，
    旧 URL 不会被新内容覆盖，可安全永久缓存。"""
    from trowel_py.app import _static_cache_headers
    root = tmp_path
    (root / "assets").mkdir()
    js = root / "assets" / "index-O9voxxvV.js"
    js.write_text("console.log(1)")
    headers = _static_cache_headers(js, root)
    assert headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_cache_headers_non_asset_file_no_cache(tmp_path: Path):
    """根目录下不带 hash 的文件（如 vite.svg）保守走 no-cache。"""
    from trowel_py.app import _static_cache_headers
    root = tmp_path
    svg = root / "vite.svg"
    svg.write_text("<svg/>")
    headers = _static_cache_headers(svg, root)
    assert headers["cache-control"] == "no-cache"


def test_spa_fallback_serves_index_with_no_cache(web_dist):
    """GET / 的 index.html 必须带 no-cache（核心修复：build 后浏览器立即拿新入口）。"""
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_spa_fallback_serves_hashed_asset_with_immutable(web_dist):
    """GET /assets/index-<hash>.js 必须带 immutable，让浏览器长缓存。"""
    (web_dist / "assets").mkdir()
    (web_dist / "assets" / "index-Abc123.js").write_text("console.log(1)")
    client = TestClient(create_app())
    resp = client.get("/assets/index-Abc123.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_resolve_web_dist_prefers_source_tree_over_packaged_static(tmp_path: Path):
    """Editable install: both web/dist (fresh build) and trowel_py/static
    (stale pip-install snapshot) exist → web/dist wins. Fixes the dev loop
    where frontend edits appeared to "not take effect" because the stale
    static copy was served first."""
    here = tmp_path / "trowel_py"
    web_dist = tmp_path / "web" / "dist"
    static = here / "static"
    web_dist.mkdir(parents=True)
    static.mkdir(parents=True)
    (web_dist / "index.html").write_text("fresh build")
    (static / "index.html").write_text("stale snapshot")
    assert _resolve_web_dist(here) == web_dist


def test_resolve_web_dist_falls_back_to_packaged_static(tmp_path: Path):
    """Packaged install (pip install .): web/ isn't shipped to site-packages,
    so fall back to the bundled trowel_py/static copy."""
    here = tmp_path / "trowel_py"
    static = here / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("packaged")
    assert _resolve_web_dist(here) == static


def test_resolve_web_dist_returns_none_when_no_build(tmp_path: Path):
    """Dev mode before any `bun run build`: neither exists → None (SPA route
    is not registered)."""
    here = tmp_path / "trowel_py"
    here.mkdir(parents=True)
    assert _resolve_web_dist(here) is None


# === slice-039: layer-one bootstrap on startup ===


def test_bootstrap_layer_one_creates_core_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """lifespan seeds layer-one core.md on startup (idempotent; C-5 no overwrite)."""
    import trowel_py.memory.paths as paths
    from trowel_py.app import bootstrap_layer_one

    monkeypatch.setattr(paths, "resolve_memory_root", lambda: tmp_path)
    assert bootstrap_layer_one() is True  # seeded
    assert (tmp_path / "core.md").exists()
    assert bootstrap_layer_one() is False  # already exists → no overwrite


def test_bootstrap_layer_one_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """bootstrap failure must not break app startup."""
    import trowel_py.memory.paths as paths
    from trowel_py.app import bootstrap_layer_one

    def boom() -> Path:
        raise RuntimeError("no home dir")

    monkeypatch.setattr(paths, "resolve_memory_root", boom)
    assert bootstrap_layer_one() is False  # swallowed, not raised
