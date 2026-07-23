from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trowel_py.app import create_app, _resolve_web_dist


@pytest.fixture
def web_dist(tmp_path: Path, monkeypatch) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>SPA</html>")
    monkeypatch.setattr("trowel_py.app._find_web_dist", lambda: dist)
    return dist


@pytest.fixture(autouse=True)
def _isolate_memory_root(tmp_path: Path, monkeypatch) -> None:
    # lifespan 会写真实 memory 并启动 review，这里同时隔离存储并禁用调度。
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
    client = TestClient(create_app())
    resp = client.get("/cc/some-deep-route")
    assert resp.status_code == 200
    assert b"SPA" in resp.content


def test_spa_fallback_blocks_path_traversal(tmp_path: Path, web_dist):
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    client = TestClient(create_app())
    resp = client.get("/%2e%2e/secret.txt")
    assert resp.status_code == 200
    assert b"TOPSECRET" not in resp.content


def test_api_miss_returns_404_not_index(web_dist):
    client = TestClient(create_app())
    resp = client.get("/api/no-such-endpoint")
    assert resp.status_code == 404


def test_health_still_responds(web_dist):
    client = TestClient(create_app())
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# index.html 会指向随构建变化的 hash 资源，因此入口要重新验证，hash 资源则可永久缓存。
def test_static_cache_headers_index_html_no_cache(tmp_path: Path):
    from trowel_py.app import _static_cache_headers

    root = tmp_path
    idx = root / "index.html"
    idx.write_text("<html>")
    headers = _static_cache_headers(idx, root)
    assert headers["cache-control"] == "no-cache"


def test_static_cache_headers_hashed_asset_immutable(tmp_path: Path):
    from trowel_py.app import _static_cache_headers

    root = tmp_path
    (root / "assets").mkdir()
    js = root / "assets" / "index-O9voxxvV.js"
    js.write_text("console.log(1)")
    headers = _static_cache_headers(js, root)
    assert headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_cache_headers_non_asset_file_no_cache(tmp_path: Path):
    from trowel_py.app import _static_cache_headers

    root = tmp_path
    svg = root / "vite.svg"
    svg.write_text("<svg/>")
    headers = _static_cache_headers(svg, root)
    assert headers["cache-control"] == "no-cache"


def test_spa_fallback_serves_index_with_no_cache(web_dist):
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_spa_fallback_serves_hashed_asset_with_immutable(web_dist):
    (web_dist / "assets").mkdir()
    (web_dist / "assets" / "index-Abc123.js").write_text("console.log(1)")
    client = TestClient(create_app())
    resp = client.get("/assets/index-Abc123.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_resolve_web_dist_prefers_fresh_source_build_over_packaged_snapshot(
    tmp_path: Path,
):
    here = tmp_path / "trowel_py"
    web_dist = tmp_path / "web" / "dist"
    static = here / "static"
    web_dist.mkdir(parents=True)
    static.mkdir(parents=True)
    (web_dist / "index.html").write_text("fresh build")
    (static / "index.html").write_text("stale snapshot")
    assert _resolve_web_dist(here) == web_dist


def test_resolve_web_dist_falls_back_to_packaged_static(tmp_path: Path):
    here = tmp_path / "trowel_py"
    static = here / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("packaged")
    assert _resolve_web_dist(here) == static


def test_resolve_web_dist_returns_none_when_no_build(tmp_path: Path):
    here = tmp_path / "trowel_py"
    here.mkdir(parents=True)
    assert _resolve_web_dist(here) is None


def test_bootstrap_layer_one_creates_core_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import trowel_py.memory.paths as paths
    from trowel_py.app import bootstrap_layer_one

    monkeypatch.setattr(paths, "resolve_memory_root", lambda: tmp_path)
    assert bootstrap_layer_one() is True
    assert (tmp_path / "core.md").exists()
    assert bootstrap_layer_one() is False


def test_bootstrap_failure_does_not_break_app_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trowel_py.memory.paths as paths
    from trowel_py.app import bootstrap_layer_one

    def boom() -> Path:
        raise RuntimeError("no home dir")

    monkeypatch.setattr(paths, "resolve_memory_root", boom)
    assert bootstrap_layer_one() is False
