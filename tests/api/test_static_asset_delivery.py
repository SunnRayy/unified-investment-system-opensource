"""Static assets must ship compressed and cacheable.

Measured against the live service on 2026-09-03: `/assets/index-*.js` came back
as 1,809,217 bytes with **no `content-encoding`** even when the request offered
`gzip, deflate, br`, and with **no `Cache-Control`** at all. The same bundle
gzips to 466,240 bytes — 74% smaller — so every uncached page load moved 1.34 MB
more than it needed to, over a link whose round trip is ~250 ms, onto a single
shared vCPU that was simultaneously serving the ~17 API calls the dashboard
fires on mount.

Compression happens at image build time (see the Dockerfile), not in
`GZipMiddleware`. That is deliberate: Starlette's `GZipResponder` writes each
chunk into a `GzipFile` and never flushes while `more_body` is true, so a
`text/event-stream` response accumulates in the compressor instead of reaching
the client — adding it would have hung the live sync log stream.

These tests run against the real `output/ux-command-center` tree rather than a
fixture, because the thing under test is precisely how the shipped build is
served. They skip when that tree has not been built.
"""

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC_DIR = Path(__file__).parents[2] / "output" / "ux-command-center"

pytestmark = pytest.mark.skipif(
    not (STATIC_DIR / "index.html").is_file(),
    reason="frontend not built (run: cd ux-command-center && npm run build)",
)


@pytest.fixture(scope="module")
def asset_paths():
    """A hashed JS asset that has a build-time .gz sibling."""
    for js in sorted((STATIC_DIR / "assets").glob("index-*.js")):
        if js.with_suffix(".js.gz").is_file():
            return f"/assets/{js.name}"
    pytest.skip("no pre-compressed asset present; run the Dockerfile gzip step")


@pytest.fixture(scope="module")
def client():
    import os

    os.environ["UIS_SERVE_STATIC"] = "1"
    os.environ["UIS_SKIP_DB_STARTUP_VALIDATION"] = "1"
    import src.api.main as main_module

    main_module = importlib.reload(main_module)
    with TestClient(main_module.app) as c:
        yield c


def test_hashed_asset_is_served_gzipped_when_accepted(client, asset_paths):
    r = client.get(asset_paths, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip", (
        "the bundle went out uncompressed — this is the 1.34 MB of avoidable "
        f"transfer per load that this file exists to prevent: {dict(r.headers)}"
    )


def test_gzipped_asset_keeps_the_javascript_media_type(client, asset_paths):
    """`.js.gz` sniffs as application/gzip, which a browser downloads instead of
    executing. The media type must come from the uncompressed name."""
    r = client.get(asset_paths, headers={"Accept-Encoding": "gzip"})
    assert "javascript" in r.headers["content-type"], r.headers["content-type"]


def test_compression_actually_shrinks_the_payload(client, asset_paths):
    """Anti-vacuity: prove the gzip sibling is smaller than the original, so
    'content-encoding: gzip' is a saving and not just a header."""
    raw = (STATIC_DIR / asset_paths.lstrip("/")).stat().st_size
    gz = (STATIC_DIR / (asset_paths.lstrip("/") + ".gz")).stat().st_size
    assert gz < raw * 0.6, f"gzip saved only {100 - gz / raw * 100:.0f}% ({raw} -> {gz})"


def test_client_without_gzip_still_gets_the_asset(client, asset_paths):
    r = client.get(asset_paths, headers={"Accept-Encoding": "identity"})
    assert r.status_code == 200
    assert "content-encoding" not in r.headers
    assert len(r.content) > 0


def test_hashed_assets_are_immutable(client, asset_paths):
    """Vite content-hashes asset filenames, so the bytes at a URL never change."""
    r = client.get(asset_paths)
    cc = r.headers.get("cache-control", "")
    assert "immutable" in cc and "max-age=31536000" in cc, r.headers


def test_index_html_is_never_cached(client):
    """index.html names the current asset hashes. Caching it pins a browser to a
    stale deployment — the one file that must always be revalidated."""
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache", r.headers


def test_spa_route_falls_back_to_index_and_is_not_cached(client):
    r = client.get("/some/client/side/route")
    assert r.status_code == 200
    assert r.headers.get("cache-control") == "no-cache"
    assert "<div id=\"root\">" in r.text


def test_gz_sibling_is_not_addressable_on_its_own(client, asset_paths):
    """`.gz` files are encodings of their siblings, not resources. Serving one
    directly would hand the client a gzip blob it never asked to decode."""
    r = client.get(asset_paths + ".gz")
    assert "javascript" not in r.headers.get("content-type", "")
    # b"\x1f\x8b" is the gzip magic number — the raw archive must not come back.
    assert not r.content.startswith(b"\x1f\x8b")


def test_vary_is_set_when_the_response_depends_on_accept_encoding(client, asset_paths):
    """Without Vary, a shared cache can hand a gzipped body to a client that
    told us it could not read one."""
    r = client.get(asset_paths, headers={"Accept-Encoding": "gzip"})
    assert "accept-encoding" in r.headers.get("vary", "").lower(), r.headers


def test_traversal_outside_the_static_root_is_refused(client):
    """Pre-existing guard — re-asserted because this handler was just rewritten."""
    r = client.get("/../../../etc/passwd")
    assert "root:" not in r.text
