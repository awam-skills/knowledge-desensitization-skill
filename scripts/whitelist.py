# AI生成
"""白名单管理：把「确认不是秘密」的值加入 config/whitelist.json。

设计原则：白名单只存 sha256(值)[:8] 指纹，不存明文 → 本文件可入库、可发布。

目录无关：--data-root 定位 config/whitelist.json。

用法
    python whitelist.py --data-root <数据根> add "VALUE"
    python whitelist.py --data-root <数据根> add --fp 1a2b3c4d
    python whitelist.py --data-root <数据根> list
    python whitelist.py --data-root <数据根> rm --fp 1a2b3c4d
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import value_fp  # noqa: E402
import paths  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("action", choices=["add", "rm", "list"])
    ap.add_argument("value", nargs="?", help="要排除的字符串（add 时给）")
    ap.add_argument("--fp", help="直接给 8 位指纹")
    a = ap.parse_args()

    root = paths.resolve_data_root(a.data_root)
    wl = os.path.join(paths.config_dir(root), "whitelist.json")

    def load() -> dict:
        if os.path.exists(wl):
            with open(wl, encoding="utf-8") as fh:
                return json.load(fh)
        return {"version": 1, "fingerprints": []}

    def save(cfg: dict) -> None:
        cfg["fingerprints"] = sorted(set(cfg.get("fingerprints", [])))
        os.makedirs(os.path.dirname(wl), exist_ok=True)
        with open(wl, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)

    cfg = load()
    fps = set(cfg.get("fingerprints", []))

    if a.action == "list":
        if not fps:
            print("(白名单为空)")
        for f in sorted(fps):
            print(f)
        return 0

    fp = a.fp or (value_fp(a.value) if a.value else None)
    if not fp:
        print("需要 --fp 或一个值", file=sys.stderr)
        return 2

    if a.action == "add":
        fps.add(fp)
        print(f"已加入白名单：{fp}")
    else:
        fps.discard(fp)
        print(f"已移出白名单：{fp}")

    cfg["fingerprints"] = sorted(fps)
    save(cfg)
    print(f"当前白名单 {len(fps)} 项 → {wl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())