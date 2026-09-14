def test_mcp_server_imports_with_supported_sdk():
    from src.mcp_server import mcp

    assert mcp.name == "BTG CMF Assistant"
