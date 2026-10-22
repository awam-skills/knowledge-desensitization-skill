# AI生成
"""agent_secret_mcp.py — 供 Agent「使用」密钥的 MCP 薄壳（stdio / JSON-RPC 2.0）。

设计（ADR-001 D4）
- 只暴露 3 个工具：secret_list / secret_run / secret_audit —— **没有 secret_get**。
  值永不出现在 MCP 响应里；secret_run 返回的是【已二次脱敏】的 stdout/stderr + 退出码。
- 与 CLI **共用同一内核**（直接 `import agent_secret`）：策略 / 审计 / 窗口 / 限额完全一致，不分叉。
- 身份：环境变量 `AGENT_SECRET_TOKEN`（由 MCP 客户端在配置的 env 里提供，不由本进程管理）。

用法
    python scripts/agent_secret_mcp.py --id <源id>
    python scripts/agent_secret_mcp.py --data-root <秘密材料目录>

客户端配置（推荐，技能作为唯一入口，用户无需自己管 MCP）
    python scripts/agent_secret.py mcp --id <源id>      # 打印可直接粘贴的 .mcp.json 片段

安全
- 不返回任何条目明文；secret_run 的输出在回传前先用 map.json 全量真值二次脱敏。
- 审计写同一份 <km>/reports/secret-access.jsonl（channel=mcp）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import agent_secret as core  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "agent-secret"
SERVER_VERSION = "0.2.0"

TOOLS = [
    {
        "name": "secret_list",
        "description": ("列出可用密钥条目的**元数据**（名/标签/凭据位/敏感级），按本次 caller 的授权过滤。"
                        "永不含值。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tag": {"type": "string", "description": "只看某标签（如 llm-key）"},
                "limit": {"type": "integer", "description": "最多返回条数（默认 200）"},
            },
        },
    },
    {
        "name": "secret_run",
        "description": ("把某条目的值注入子进程环境变量后执行命令，返回**已二次脱敏**的 stdout/stderr "
                        "与退出码。值绝不返回；越权/超限时 isError=true。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "条目名（形如 [REDACTED:label:hex]）"},
                "cmd": {"type": "array", "items": {"type": "string"},
                        "description": "要执行的命令与参数（argv 数组）"},
                "as_env": {"type": "string", "description": "注入的环境变量名（默认 SECRET）"},
            },
            "required": ["name", "cmd"],
        },
    },
    {
        "name": "secret_audit",
        "description": "读取访问审计尾部（结构化，不含值）。",
        "inputSchema": {
            "type": "object",
            "properties": {"tail": {"type": "integer", "description": "显示末 N 条（默认 50）"}},
        },
    },
]


def _text(payload) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def call_tool(root: str, token: str, name: str, a: dict) -> dict:
    """执行一个 MCP 工具调用。**不提供任何读值工具**。"""
    if name == "secret_list":
        policy = core.load_policy(root)
        entries = core.load_entries(root)
        principal, how, cfg = core.resolve_caller(root, token)
        unlock = core.window_active(root)
        tag = a.get("tag")
        limit = int(a.get("limit") or 200)
        rows, denied = [], 0
        for en in sorted(entries):
            ent = entries[en]
            if tag and ent.get("label") != tag:
                continue
            ok, _reason = core.judge(policy, cfg, principal, "list", en, ent, unlock)
            if ok:
                if len(rows) < limit:
                    rows.append({"name": en, "label": ent.get("label"),
                                 "credential": bool(ent.get("credential")),
                                 "sensitivity": core.SENSITIVITY.get(ent.get("label"), "?")})
            else:
                denied += 1
        core.audit_append(root, {"ts": core._now(), "principal": principal, "channel": "mcp",
                                 "action": "list", "entry": tag or "", "result": "ok",
                                 "reason": "", "ttl": 0, "cmd_hash": ""})
        return _text({"principal": principal, "how": how, "count": len(rows),
                      "denied": denied, "entries": rows})

    if name == "secret_run":
        cmd = a.get("cmd") or []
        if isinstance(cmd, str):
            cmd = [cmd]
        res = core.run_secret(root, a.get("name") or "", list(cmd), as_env=a.get("as_env"),
                              as_file=None, token=token, channel="mcp")
        payload = {"exit_code": res["exit"], "redacted": res["redacted"],
                   "stdout": res["stdout"], "stderr": res["stderr"]}
        if not res["ok"]:
            payload.update(denied=res["denied"], reason=res["reason"])
            return {"content": [{"type": "text",
                                 "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
                    "isError": True}
        return _text(payload)

    if name == "secret_audit":
        n = int(a.get("tail") or 50)
        rows = core.audit_rows(root, n)
        principal, _how, _cfg = core.resolve_caller(root, token)
        core.audit_append(root, {"ts": core._now(), "principal": principal, "channel": "mcp",
                                 "action": "audit", "entry": "", "result": "ok",
                                 "reason": "", "ttl": 0, "cmd_hash": ""})
        return _text({"count": len(rows), "records": rows})

    if name in ("secret_get", "secret_show", "secret_read", "secret_value", "secret_print"):
        return _error("本 broker **不提供读值工具**（设计红线：只暴露「用」，不暴露「读」）。"
                      "请改用 secret_run 把值注入子进程 env。")

    return _error("未知工具：%s（可用：secret_list / secret_run / secret_audit）" % name)


def handle(root: str, token: str, req: dict):
    """处理一条 JSON-RPC 消息；通知类返回 None（按协议不回响应）。"""
    rid = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }}
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        res = call_tool(root, token, params.get("name") or "", params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": rid, "result": res}
    if rid is None:
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32601, "message": "未实现的方法：%s" % method}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="供 Agent「使用」密钥的 MCP 薄壳（stdio/JSON-RPC）")
    ap.add_argument("--id", help="源 id（→ <秘密材料根>/<id>）")
    ap.add_argument("--data-root", help="直接指定秘密材料目录")
    ap.add_argument("--secrets-root", help="秘密材料根")
    args = ap.parse_args(argv)
    root = core.resolve_secret_dir(args)
    token = os.environ.get("AGENT_SECRET_TOKEN", "")
    for line in sys.stdin:                      # MCP stdio：一行一条 JSON-RPC
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(root, token, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())