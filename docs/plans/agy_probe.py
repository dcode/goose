#!/usr/bin/env python3
"""Probe `agy acp` over one stdio connection and dump what goose needs to know.

Usage:
    python3 docs/plans/agy_probe.py [--agy PATH] [--mcp-cmd "npx -y @modelcontextprotocol/server-everything"]
        [--prompt] [--out docs/plans/agy-acp-probe.json]

Steps: initialize -> session/new (no auth call) -> on -32000, authenticate(oauth-personal) and retry
-> optionally session/new with a stdio MCP server -> optionally a one-line prompt.
Server->client requests (permissions, fs, terminal) are logged and refused.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

AUTH_REQUIRED = -32000
Json = dict[str, Any]


class AcpConn:
    def __init__(self, proc: asyncio.subprocess.Process, log: list[Json]) -> None:
        self.proc = proc
        self.log = log
        self.next_id = 0
        self.pending: dict[int, asyncio.Future[Json]] = {}
        self.reader = asyncio.create_task(self._read_loop())

    async def _send(self, msg: Json) -> None:
        assert self.proc.stdin
        self.log.append({"dir": "->", **msg})
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        await self.proc.stdin.drain()

    async def request(self, method: str, params: Json, timeout: float = 60) -> Json:
        msg_id = self.next_id
        self.next_id += 1
        fut: asyncio.Future[Json] = asyncio.get_running_loop().create_future()
        self.pending[msg_id] = fut
        await self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return await asyncio.wait_for(fut, timeout)

    async def _read_loop(self) -> None:
        assert self.proc.stdout
        while line := await self.proc.stdout.readline():
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                print(f"[non-json] {line.decode(errors='replace').rstrip()}", file=sys.stderr)
                continue
            self.log.append({"dir": "<-", **msg})
            match msg:
                case {"id": int(i), "method": method}:
                    print(f"[server request] {method}: {json.dumps(msg.get('params'))[:300]}")
                    await self._send({"jsonrpc": "2.0", "id": i, "error": {"code": -32601, "message": "probe refuses"}})
                case {"id": int(i)} if i in self.pending:
                    self.pending.pop(i).set_result(msg)
                case {"method": "session/update", "params": params}:
                    update = params.get("update", {})
                    kind = update.get("sessionUpdate")
                    text = update.get("content", {}).get("text", "") if isinstance(update.get("content"), dict) else ""
                    print(f"[update] {kind} {text!r}"[:200])
                case _:
                    print(f"[notification] {json.dumps(msg)[:300]}")


def summarize_session(label: str, resp: Json) -> None:
    print(f"\n=== {label} ===")
    if "error" in resp:
        print("ERROR:", resp["error"])
        return
    result = resp["result"]
    print("sessionId:", result.get("sessionId"))
    print("modes:", json.dumps(result.get("modes"), indent=2))
    print("models (legacy session/set_model):", json.dumps(result.get("models"), indent=2))
    for opt in result.get("configOptions") or []:
        values = [o.get("value") or o.get("id") for o in opt.get("options", [])]
        print(f"configOption id={opt.get('id')!r} category={opt.get('category')!r} "
              f"current={opt.get('currentValue')!r} values={values}")
    print("other keys:", sorted(set(result) - {"sessionId", "modes", "models", "configOptions"}))


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agy", default="agy")
    ap.add_argument("--mcp-cmd", help="stdio MCP server command to pass in session/new")
    ap.add_argument("--prompt", action="store_true", help="send a short prompt to the first session")
    ap.add_argument("--out", default=str(Path(__file__).with_name("agy-acp-probe.json")))
    args = ap.parse_args()

    log: list[Json] = []
    proc = await asyncio.create_subprocess_exec(
        args.agy, "acp", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE
    )
    conn = AcpConn(proc, log)
    cwd = os.getcwd()
    try:
        init = await conn.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            "clientInfo": {"name": "goose-probe", "version": "0.0.1"},
        })
        print("initialize ok:", init.get("result", {}).get("agentInfo"))

        new = await conn.request("session/new", {"cwd": cwd, "mcpServers": []})
        needed_authenticate = new.get("error", {}).get("code") == AUTH_REQUIRED
        if needed_authenticate:
            print("session/new -> AUTH_REQUIRED after initialize; trying authenticate(oauth-personal)")
            auth = await conn.request("authenticate", {"methodId": "oauth-personal"})
            print("authenticate:", auth)
            new = await conn.request("session/new", {"cwd": cwd, "mcpServers": []})
        summarize_session("session/new (no MCP)", new)

        if args.mcp_cmd and "result" in new:
            cmd, *cmd_args = shlex.split(args.mcp_cmd)
            mcp = await conn.request("session/new", {"cwd": cwd, "mcpServers": [
                {"name": "probe", "command": cmd, "args": cmd_args, "env": []}
            ]}, timeout=120)
            summarize_session("session/new (stdio MCP)", mcp)

        if args.prompt and "result" in new:
            sid = new["result"]["sessionId"]
            resp = await conn.request("session/prompt", {
                "sessionId": sid,
                "prompt": [{"type": "text", "text": "Reply with exactly: pong. Then list the names of the MCP tools you have, if any."}],
            }, timeout=180)
            print("\nprompt stopReason:", resp.get("result", resp.get("error")))

        print(f"\nneeded explicit authenticate: {needed_authenticate}")
    finally:
        Path(args.out).write_text(json.dumps(log, indent=2))
        print(f"wrote raw transcript to {args.out}")
        proc.terminate()
        await proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
