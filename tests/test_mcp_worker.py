from mcp_worker import build_app


def test_mcp_worker_exposes_streamable_http_and_legacy_sse_routes():
    paths = {getattr(route, "path", None) for route in build_app().routes}

    assert "/" in paths
    assert "/sse" in paths
    assert "/messages" in paths
