# AI生成
"""restore.py — 把镜像里的某个占位符「方便地填回原文」。

背景（用户决策）
- 基线上宁可「过度脱敏」：任何看起来像密钥/密码的串一律替换，保证不落下。
- 代价是会有误报。发现某个占位符其实不是敏感信息，就用本工具把它按需还原
  （只改你指定的那一处 / 那个文件 / 那个 token），无需重跑整条管线。

安全
- 原文来自 vault/map/map.json（SECRET，仅本地），**绝不打印**、绝不进报告。
- 默认 dry-run，必须 --yes 才写盘。
- 每次操作追加审计 `vault/map/restore-log.jsonl`（只记 token/路径/行号，不记原文）。
- credential=true 的 token（已搬进 Bitwarden 的真凭据）还原时会额外警告。

目录无关
- --data-root 定位 vault/ 与镜像；--mirror 缺省读 <data-root>/workspace.json 的 mirror。

用法
    python restore.py --data-root <数据根> list [--credential]
    python restore.py --data-root <数据根> show --token [REDACTED:secret:1a2b3c4d]
    python restore.py --data-root <数据根> apply --token ... --file pages/env-AI.md [--yes]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import TOKEN_RX, shape  # noqa: E402
import paths  # noqa: E402


def _map_path(root: str) -> str:
    return os.path.join(paths.map_dir(root), "map.json")


def _log_path(root: str) -> str:
    return os.path.join(paths.map_dir(root), "restore-log.jsonl")


def load_map(root: str) -> dict:
    mp = _map_path(root)
    if not os.path.exists(mp):
        print("找不到映射表：", mp)
        raise SystemExit(2)
    with open(mp, encoding="utf-8") as fh:
        return json.load(fh).get("tokens", {})


def audit(root: str, rec: dict) -> None:
    lp = _log_path(root)
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    rec["at"] = dt.datetime.now().isoformat(timespec="seconds")
    with open(lp, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def cmd_list(args) -> int:
    tokens = load_map(args._root)
    rows = []
    for tok, meta in tokens.items():
        if args.credential and not meta.get("credential"):
            continue
        v = meta.get("value") or ""
        rows.append((tok, meta.get("rule", ""), meta.get("label", ""),
                     v, len(meta.get("occurrences", []))))
    rows.sort(key=lambda r: (r[2], r[0]))
    print(f"共 {len(rows)} 个占位符"
          f"{'（仅 credential）' if args.credential else ''}\n")
    print(f"{'token':<40} {'label':<10} {'位数':>4} {'形状':<28} {'出现':>4}")
    print("-" * 100)
    for tok, rule, label, v, occ in rows:
        sh = shape(v)
        if len(sh) > 26:
            sh = sh[:26] + "…"
        print(f"{tok:<40} {label:<10} {len(v):>4} {sh:<28} {occ:>4}")
    print("\n提示：形状把 a/A/9/符号分开，用于人工判断像不像密钥，不泄露原值。")
    return 0


def cmd_show(args) -> int:
    tokens = load_map(args._root)
    meta = tokens.get(args.token)
    if not meta:
        cands = [t for t in tokens if t.endswith(f":{args.token}]")]
        if len(cands) == 1:
            args.token = cands[0]
            meta = tokens[cands[0]]
    if not meta:
        print("没有找到该 token。")
        return 1
    v = meta.get("value") or ""
    print("token    :", args.token)
    print("rule     :", meta.get("rule"))
    print("label    :", meta.get("label"))
    print("credential:", meta.get("credential"))
    print("值长度   :", len(v))
    print("值形状   :", shape(v))
    print("出现位置 :")
    for o in meta.get("occurrences", []):
        print(f"  {o['path']}  第 {o['line']} 行")
    return 0


def cmd_apply(args) -> int:
    tokens = load_map(args._root)
    meta = tokens.get(args.token)
    if not meta:
        cands = [t for t in tokens if t.endswith(f":{args.token}]")]
        if len(cands) == 1:
            args.token, meta = cands[0], tokens[cands[0]]
    if not meta:
        print("没有找到该 token。")
        return 1
    value = meta.get("value")
    if value is None:
        print("该 token 没有存原文（可能来自旧版规则），无法还原。")
        return 1

    mirror = args.mirror or paths.workspace_mirror(args._root)
    if not os.path.isdir(mirror):
        print(f"镜像目录不存在：{mirror}", file=sys.stderr)
        return 2
    targets = []
    for o in meta.get("occurrences", []):
        if args.file and os.path.normpath(o["path"]) != os.path.normpath(args.file):
            continue
        p = os.path.join(mirror, o["path"])
        if not os.path.exists(p):
            continue
        targets.append(p)
    if not targets:
        print("在镜像里没有找到可还原的位置"
              f"（--file={args.file!r}）。")
        return 1

    if meta.get("credential"):
        print("⚠️  该占位符对应「真凭据」（已搬入 Bitwarden）。"
              "还原会在镜像里留下明文，通常不该这么做。")

    tok_rx = re.compile(re.escape(args.token))
    changed = 0
    for p in sorted(set(targets)):
        with open(p, encoding="utf-8", newline="") as fh:
            text = fh.read()
        n = len(tok_rx.findall(text))
        if not n:
            continue
        new = tok_rx.sub(lambda _m: value, text)
        rel = os.path.relpath(p, mirror)
        if args.yes:
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(new)
            audit(args._root, {"action": "restore", "token": args.token, "file": rel,
                               "count": n, "bytes_in": len(text), "bytes_out": len(new)})
            print(f"已还原 {rel}：{n} 处")
        else:
            print(f"[dry-run] {rel}：将还原 {n} 处")
        changed += n

    if not args.yes:
        print("\n[dry-run] 未写盘。加 --yes 执行。")
    print("合计:", changed, "处")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list")
    p.add_argument("--credential", action="store_true", help="只看真凭据")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show")
    p.add_argument("--token", required=True)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("apply")
    p.add_argument("--token", required=True)
    p.add_argument("--file", help="只还原该文件（镜像相对路径）")
    p.add_argument("--mirror", help="镜像根；缺省读 workspace.json 的 mirror")
    p.add_argument("--yes", action="store_true", help="真正写盘")
    p.set_defaults(fn=cmd_apply)

    args = ap.parse_args()
    args._root = paths.resolve_data_root(args.data_root)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())