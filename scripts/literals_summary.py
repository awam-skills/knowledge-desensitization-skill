# AI生成
"""安全摘要 literals.json —— 只输出结构与长度，绝不打印任何片段明文。

背景：一次临时自检脚本误将 pair 条目的 parts 原值打印进了会话。
本脚本作为唯一允许的「查看 literals 结构」入口，从根上避免此类泄露。

目录无关：--data-root 定位 config/literals.json。

用法：
    python scripts/literals_summary.py --data-root <根>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def _safe_part(p: str) -> str:
    """占位符可安全显示；真实值一律只回长度。"""
    if p.startswith("__") and p.endswith("__"):
        return p
    return "<len=%d>" % len(p)


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--show-placeholders", action="store_true",
                    help="（默认已开启）只显示占位符形状")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    lp = os.path.join(paths.config_dir(root), "literals.json")
    if not os.path.exists(lp):
        print("尚无 literals.json（未配置任何字面量）：", lp)
        return 0
    cfg = json.load(open(lp, encoding="utf-8"))
    items = cfg.get("items", [])
    print("items_count =", len(items))
    for it in items:
        match = it.get("match")
        parts = it.get("parts") or []
        parts_show = "[" + ", ".join(_safe_part(p) for p in parts) + "]" if parts else "-"
        print("  id=%-14s match=%-8s enabled=%s credential=%s "
              "require_keyword=%s single_mode=%s parts=%s"
              % (it.get("id"), match, it.get("enabled"), it.get("credential"),
                 it.get("require_keyword"), it.get("single_mode"), parts_show))
    print("\n提示：真实片段只以 <len=N> 呈现；占位符以 __XXX__ 呈现。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())