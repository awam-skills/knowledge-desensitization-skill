# AI生成
"""幂等地往 config/literals.json 追加一条 pair（组合型）字面量条目。

目录无关：--data-root 定位 config/literals.json（不存在则按 example 初始化）。

用法
    python scripts/add_pair_literal.py --data-root <根> \
        --id my-combo-pw2 --parts __PART_C__,__PART_D__

安全约束
- 只做「读取 JSON → 追加一条模板 → 写回」，绝不打印任何已有明文值。
- 按 --id 幂等：该 id 已存在则跳过。
- parts 只放占位符，由用户在本机编辑替换（不经过 Agent）。

新增条目默认与已验证的工作配置一致：
    require_keyword = false   # 无需关键词，直接匹配裸组合 .*A…B.* / .*B…A.*
    single_mode     = anywhere # 单片段同行任意位置出现即命中（.*A.* / .*B.*）
    orders          = both
    single          = true
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--id", default="my-combo-pw", help="条目唯一 id（幂等键）")
    ap.add_argument("--label", default="pw")
    ap.add_argument("--parts", default="__PART_A__,__PART_B__",
                    help="逗号分隔的片段占位符（≥2）")
    ap.add_argument("--pad", type=int, default=32)
    ap.add_argument("--wclass", default=r"[A-Za-z0-9!@#$%^&*_\-+/.=@]")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    lp = os.path.join(paths.config_dir(root), "literals.json")

    if not os.path.exists(lp):
        ex = os.path.join(paths.config_dir(root), "literals.example.json")
        if os.path.exists(ex):
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            shutil.copyfile(ex, lp)
            print("已从 literals.example.json 初始化", lp)
        else:
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            with open(lp, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "items": []}, fh, ensure_ascii=False, indent=2)
            print("已新建空的", lp)

    with open(lp, encoding="utf-8") as fh:
        cfg = json.load(fh)
    items = cfg.setdefault("items", [])

    if any(it.get("id") == args.id for it in items):
        print("已存在 id=%s，未改动。" % args.id)
        return 0

    parts = [p.strip() for p in args.parts.split(",") if p.strip()]
    if len(parts) < 2:
        print("parts 至少 2 个")
        return 2

    ref = next((it for it in items if it.get("match") in ("context", "literal", "pair")), None)
    kw = (ref.get("keywords") if ref else None) or [
        "密码", "口令", "密钥", "password", "passwd", "pwd", "密保"]
    sep = (ref.get("separator") if ref else None) or r"\s*[:：=＝]?\s*"
    ww = ref.get("whole_word", True) if ref else True

    pair = {
        "id": args.id,
        "label": args.label,
        "enabled": True,
        "credential": True,
        "match": "pair",
        "parts": parts,
        "orders": "both",
        "wclass": args.wclass,
        "pad": args.pad,
        "single": True,
        "require_keyword": False,
        "single_mode": "anywhere",
        "keywords": kw,
        "separator": sep,
        "whole_word": ww,
        "note": "把 %s 换成本机真实的两段常用字符串；"
                "AB 与 BA 任意顺序、以及单片段（同行任意位置）都会命中"
                % "/".join(parts),
    }
    items.append(pair)

    bak = lp + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
    shutil.copyfile(lp, bak)
    with open(lp, "w", encoding="utf-8", newline="") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print("已追加 pair 条目：")
    print("  id            =", pair["id"])
    print("  parts(占位符)  =", pair["parts"])
    print("  require_keyword=", pair["require_keyword"])
    print("  single_mode   =", pair["single_mode"])
    print("  orders        =", pair["orders"])
    print("  items_total   =", len(items))
    print("  备份           =", os.path.basename(bak))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())