# AI生成
"""把「熵兜底」命中里确认是真凭据的条目，从 map.json 提升为 Bitwarden 导入清单。

背景
- 熵兜底（high_entropy_token）命中是「未知格式的随机串」，可能是真密钥，也可能是
  文档措辞/版本号。为不污染 Bitwarden，它们默认 credential=false（只替换、不入库）。
- 你在核对清单里确认某条确实是密钥 → 用本脚本按指纹把它导出成 Bitwarden 条目。

流程
    1. python scripts/promote.py --data-root <根> new
    2. python scripts/promote.py --data-root <根> add --fp 1a2b3c4d
       （可重复；fp 从 sanitize 报告的「指纹」列或 whitelist 里拿）
    3. pwsh -File scripts/bitwarden-import.ps1 -DataRoot <根> -File vault/map/bw-promoted-<ts>.json

说明
- 只读取 vault/map/map.json（含原文），输出也写在 vault/ 下，绝不打印明文。
- 找不到该指纹时，也支持按 token 直接指定：--token "[REDACTED:entropy:xxxx]"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import value_fp  # noqa: E402
import paths  # noqa: E402


def load_map(root: str) -> dict:
    mp = os.path.join(paths.map_dir(root), "map.json")
    if not os.path.exists(mp):
        sys.exit(f"找不到 {mp}")
    with open(mp, encoding="utf-8") as fh:
        return json.load(fh)


def build_item(token: str, rec: dict) -> dict:
    occ = (rec.get("occurrences") or ["?"])[0]
    src = occ if isinstance(occ, str) else occ.get("path", "?")
    return {
        "id": None, "organizationId": None, "folderId": None,
        "type": 1, "reprompt": 0,
        "name": f"{paths.ITEM_PREFIX}/{src} · {rec.get('label', '?')}"[:120],
        "notes": (f"来源: {src}\n类型: {rec.get('label')}（由熵兜底提升）\n"
                  f"占位: {token}"),
        "favorite": False,
        "login": {"username": None, "password": rec.get("value"), "uris": []},
        "fields": [{"name": paths.TOKEN_FIELD, "value": token, "type": 0}],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("action", choices=["new", "add"])
    ap.add_argument("--fp", action="append", default=[])
    ap.add_argument("--token", action="append", default=[])
    ap.add_argument("--file")
    a = ap.parse_args()

    root = paths.resolve_data_root(a.data_root)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = a.file or os.path.join(paths.map_dir(root), f"bw-promoted-{ts}.json")

    if a.action == "new":
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"encrypted": False, "folders": [], "items": []},
                      fh, ensure_ascii=False, indent=2)
        print("已新建:", out)
        return 0

    if not os.path.exists(out):
        sys.exit(f"先执行 new 创建 {out}")
    m = load_map(root).get("tokens", {})
    by_fp = {}
    for tok, rec in m.items():
        v = rec.get("value")
        if v:
            by_fp.setdefault(value_fp(v), []).append((tok, rec))

    picked = []
    for fp in a.fp:
        for tok, rec in by_fp.get(fp, []):
            picked.append((tok, rec))
    for tok in a.token:
        if tok in m:
            picked.append((tok, m[tok]))

    with open(out, encoding="utf-8") as fh:
        blob = json.load(fh)
    have = {i["fields"][0]["value"] for i in blob["items"]}
    added = 0
    for tok, rec in picked:
        if tok in have or not rec.get("value"):
            continue
        blob["items"].append(build_item(tok, rec))
        added += 1

    with open(out, "w", encoding="utf-8") as fh:
        json.dump(blob, fh, ensure_ascii=False, indent=2)
    print(f"新增 {added} 条，文件现有 {len(blob['items'])} 条 → {out}")
    if not added and not picked:
        print("没匹配到。检查指纹是否正确（报告里 8 位 hex）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())