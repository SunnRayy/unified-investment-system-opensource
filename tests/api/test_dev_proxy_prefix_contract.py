"""The dev proxy must forward /api intact — the other half of a two-part contract.

`test_cloud_run_api_prefix.py` asserts the backend half: with `UIS_AUTH_TOKEN` or
`UIS_GCS_BUCKET` set, **only** `/api/*` routes are mounted, because
`BearerTokenMiddleware` exempts non-`/api` GETs for the SPA shell and unprefixed
routers would therefore serve portfolio data unauthenticated.

Nothing asserted the consequence for local development, and the Vite proxy quietly
assumed the opposite — it stripped `/api` before forwarding, which only works while
the backend's unprefixed convenience surface exists. The two statements were
individually true and jointly broken:

    backend:  "with a token set, I serve only /api/*"
    proxy:    "I will remove /api before forwarding"

`docs/quickstart.md` tells a first-run user to export `UIS_AUTH_TOKEN` before their
first start, so following the documented steps in the documented order produced a
404 on login — the exact dead end the doc was added to close.

This file is the missing half. It is deliberately a config-contract test rather than
a behavioural one: the failure lives in a build-tool config that no Python or vitest
run exercises, which is precisely why it went unnoticed.
"""

import re
from pathlib import Path

import pytest

VITE_CONFIG = (
    Path(__file__).parents[2] / "ux-command-center" / "vite.config.ts"
)

pytestmark = pytest.mark.skipif(
    not VITE_CONFIG.is_file(), reason="vite.config.ts not present in this tree"
)


@pytest.fixture(scope="module")
def config_text() -> str:
    return VITE_CONFIG.read_text()


@pytest.fixture(scope="module")
def proxy_block(config_text: str) -> str:
    """The `proxy: { ... }` object, brace-matched from the config source."""
    start = config_text.index("proxy:")
    depth = 0
    for i in range(config_text.index("{", start), len(config_text)):
        if config_text[i] == "{":
            depth += 1
        elif config_text[i] == "}":
            depth -= 1
            if depth == 0:
                return config_text[start : i + 1]
    raise AssertionError("could not brace-match the proxy block")


def test_api_proxy_rule_exists(proxy_block: str):
    """Anti-vacuity: the assertions below are meaningless if the rule is gone."""
    assert "'/api'" in proxy_block or '"/api"' in proxy_block, proxy_block


def test_api_proxy_does_not_strip_the_prefix(proxy_block: str):
    """The whole point.

    A `rewrite` that removes `/api` routes the request to a surface the backend
    stops mounting the moment auth is enabled.
    """
    rewrites = re.findall(r"rewrite\s*:", proxy_block)
    assert not rewrites, (
        "the dev proxy rewrites a path before forwarding. If that rewrite strips "
        "/api, login 404s as soon as UIS_AUTH_TOKEN is set — which quickstart.md "
        "instructs every new user to do. See tests/api/test_cloud_run_api_prefix.py "
        f"for the backend half of this contract.\n\n{proxy_block}"
    )


def test_no_rule_claims_an_unprefixed_backend_surface(proxy_block: str):
    """Every proxied path must start `/api`.

    A rule for a bare path advertises a backend surface that does not exist in
    auth-enabled mode. The removed `/sync/stream` rule was exactly that: inert,
    but it implied a second SSE endpoint that was never mounted.
    """
    keys = re.findall(r"['\"](/[^'\"]*)['\"]\s*:\s*\{", proxy_block)
    assert keys, f"no proxy rules parsed — check this test, not the config\n{proxy_block}"
    non_api = [k for k in keys if not k.startswith("/api")]
    assert not non_api, (
        f"proxy rules for non-/api paths: {non_api}. The backend mounts unprefixed "
        "routes only when neither UIS_AUTH_TOKEN nor UIS_GCS_BUCKET is set, so these "
        "silently stop resolving in exactly the configuration the quickstart asks for."
    )


def test_proxy_targets_the_documented_backend_port(proxy_block: str):
    """CLAUDE.md and dev.sh both pin the backend to 8008; a drift here presents as
    a total dev-server failure with no obvious cause."""
    assert "localhost:8008" in proxy_block, proxy_block


def test_backend_half_of_the_contract_is_still_asserted():
    """This file is only meaningful while its counterpart exists.

    If the backend guard is ever deleted, forwarding /api intact stops being a
    requirement and these tests become cargo cult — so fail loudly instead.
    """
    counterpart = Path(__file__).parent / "test_cloud_run_api_prefix.py"
    assert counterpart.is_file(), "the backend half of this contract is missing"
    text = counterpart.read_text()
    assert "test_auth_token_without_bucket_mounts_no_unprefixed_api_routes" in text, (
        "the test asserting 'auth on => only /api/* mounted' is gone; this file's "
        "premise no longer holds and both halves need rethinking together"
    )
