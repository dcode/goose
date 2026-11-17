#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["mcp>=2.3,<3"]
# ///
"""Stdio MCP server whose tool names are sized to hit agy's tool-name limit.

agy exposes each MCP tool to the model as `mcp_<server>_<tool>`. This server picks tool
names so that the full agy name is exactly the lengths in PROBE_LENGTHS, given the server
name goose passes to agy (PROBE_SERVER, which must match the extension key).

A tool name that agy rejects may break every turn, so test one length per session:

    goose session --provider agy-acp \
      --with-extension "probe:PROBE_LENGTHS=64 uv run --quiet --script $PWD/scripts/agy-tool-name-probe.py"

Then ask: "Call every tool from the probe extension and report what each returned."
Repeat with PROBE_LENGTHS=65 and PROBE_LENGTHS=100.

Each tool name starts with its target length (`t65...`), so a truncated name still shows
which tool it was. `probe_ping` is a short control tool present in every run.
"""

import os
import sys
from collections.abc import Callable

from mcp.server.mcpserver import MCPServer

AGY_PREFIX = "mcp_"
MCP_MAX_TOOL_NAME = 128
DEFAULT_LENGTHS = "64"


def agy_tool_name(server: str, tool: str) -> str:
    return f"{AGY_PREFIX}{server}_{tool}"


def tool_name_for_total(server: str, total: int) -> str:
    """Build a tool name so that `mcp_<server>_<tool>` is exactly `total` characters."""
    overhead = len(agy_tool_name(server, ""))
    tool_len = total - overhead
    head = f"t{total}_"
    if tool_len < len(head) or tool_len > MCP_MAX_TOOL_NAME:
        raise ValueError(
            f"cannot reach total {total} with server {server!r}: tool name would be"
            f" {tool_len} chars (allowed {len(head)}..{MCP_MAX_TOOL_NAME})"
        )
    return head + "x" * (tool_len - len(head))


def parse_lengths(raw: str) -> list[int]:
    return [int(part) for part in raw.split(",") if part.strip()]


def make_tool(total: int, name: str) -> Callable[[], str]:
    def tool() -> str:
        return f"PROBE-OK total={total} tool={name}"

    return tool


def build_server(server: str, lengths: list[int]) -> MCPServer:
    mcp = MCPServer(server)

    @mcp.tool(name="probe_ping")
    def probe_ping() -> str:
        """Control tool with a short name. Returns PROBE-OK ping."""
        return "PROBE-OK ping"

    for total in lengths:
        name = tool_name_for_total(server, total)
        mcp.add_tool(
            make_tool(total, name),
            name=name,
            description=f"Returns a marker. agy should expose this as a {total}-char name.",
        )
    return mcp


def main() -> None:
    server = os.environ.get("PROBE_SERVER", "probe")
    lengths = parse_lengths(os.environ.get("PROBE_LENGTHS", DEFAULT_LENGTHS))
    for total in lengths:
        full = agy_tool_name(server, tool_name_for_total(server, total))
        print(f"probe: {len(full):>3} chars  {full}", file=sys.stderr)
    build_server(server, lengths).run()


if __name__ == "__main__":
    main()
