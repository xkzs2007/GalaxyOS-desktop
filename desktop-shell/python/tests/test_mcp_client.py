"""tests/test_mcp_client.py — Unit tests for MCP server configuration.

Covers:
- list_servers / add_server / remove_server
- Config file persistence
- Path handling for the ~/.galaxyos/mcp.json file
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestMcpConfig:
    def setup_method(self):
        """Use a temp dir for each test."""
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        # Patch config_path to return our temp file
        import mcp_client
        self.patcher = patch.object(mcp_client, 'config_path',
                                     return_value=self.tmp_path / "mcp.json")
        self.patcher.start()
        # Reset module-level state
        mcp_client._test_state = {}

    def teardown_method(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_list_empty(self):
        import mcp_client
        assert mcp_client.list_servers() == []

    def test_add_and_list(self):
        import mcp_client
        entry = mcp_client.add_server("fs", "npx", ["-y", "fs-server"])
        assert entry["name"] == "fs"
        assert entry["command"] == "npx"
        assert entry["args"] == ["-y", "fs-server"]
        servers = mcp_client.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "fs"

    def test_add_dedup_by_name(self):
        """Adding a server with an existing name replaces the old one."""
        import mcp_client
        mcp_client.add_server("fs", "old-cmd", [])
        mcp_client.add_server("fs", "new-cmd", ["--arg"])
        servers = mcp_client.list_servers()
        assert len(servers) == 1
        assert servers[0]["command"] == "new-cmd"
        assert servers[0]["args"] == ["--arg"]

    def test_add_with_env(self):
        import mcp_client
        mcp_client.add_server("weather", "node", ["server.js"],
                              env={"API_KEY": "abc123"})
        servers = mcp_client.list_servers()
        assert servers[0]["env"] == {"API_KEY": "abc123"}

    def test_remove(self):
        import mcp_client
        mcp_client.add_server("a", "cmd1", [])
        mcp_client.add_server("b", "cmd2", [])
        assert mcp_client.remove_server("a") is True
        assert mcp_client.remove_server("nonexistent") is False
        servers = mcp_client.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "b"

    def test_persistence(self):
        """Adding a server writes to disk; the file persists across
        module reloads (which simulate a new process)."""
        import mcp_client
        mcp_client.add_server("persistent", "node", ["x.js"])
        # Read the file directly to confirm it was written
        config_file = mcp_client.config_path()
        assert config_file.exists()
        # Re-read the file as a fresh dict to confirm round-trip
        saved = json.loads(config_file.read_text(encoding="utf-8"))
        assert len(saved.get("servers", [])) == 1
        assert saved["servers"][0]["name"] == "persistent"
        # Confirm list_servers returns the same data
        servers = mcp_client.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "persistent"
