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

    def _load_items(path):
        if not path or not os.path.exists(path):
            return []
        return json.load(open(path, encoding="utf-8")).get("items", [])

    items = _load_items(lp)

    # 配置分层：生效字面量 = 全局(<KM_CONFIG_HOME>/config/literals.json) → 每源(lp)，
    # 按 id 合并、**每源覆盖全局**。因此这里必须两层都看，否则用户在全局层的修改「看不见」。
    gpath = None
    gitems: list = []
    try:
        import sanitize_lib
        gdir = sanitize_lib.global_config_dir()
        if gdir:
            gpath = os.path.join(gdir, "literals.json")
        gitems = _load_items(gpath)
    except Exception as e:
        print("  （分层检查不可用：%s）" % e, file=sys.stderr)

    if not gitems and not items:
        print("尚无 literals.json（未配置任何字面量）：", lp, "或", gpath or "全局 config/literals.json")
        return 0

    # 编译检查跑在「合并后的生效集」上：不编译出来的条目 = 静默失效，必须显式告警
    live_ids: set[str] = set()
    try:
        import sanitize_lib
        for r in sanitize_lib.merge_literals([gpath, lp]):
            live_ids.add(r.name.split(":", 1)[1].split(":p")[0])
    except Exception as e:  # 编译检查不可用时仍可查看结构
        print("  （编译检查不可用：%s）" % e, file=sys.stderr)

    dropped = 0

    def _row(it) -> bool:
        """打印一条（真实值只回长度）；返回「是否不生效」。"""
        match = it.get("match")
        parts = it.get("parts") or []
        parts_show = "[" + ", ".join(_safe_part(p) for p in parts) + "]" if parts else "-"
        status, why = "生效", ""
        if it.get("id") not in live_ids:
            status = "!! 不生效（被丢弃）"
            base = it.get("base")
            if it.get("enabled") is False:
                why = "enabled=false"
            elif match == "literal" and isinstance(base, str) \
                    and len(base) < it.get("min_len_guard", 8):
                why = "literal 模式要求 base>=min_len_guard(默认8)，当前长度 %d" % len(base)
            elif match in ("pair", "combo") and not parts:
                why = "%s 模式缺少 parts" % match
            else:
                why = "未能编译出规则（检查 match/字段拼写）"
        print("  id=%-16s match=%-8s enabled=%s credential=%s "
              "require_keyword=%s single_mode=%s parts=%s  -> %s%s"
              % (it.get("id"), match, it.get("enabled"), it.get("credential"),
                 it.get("require_keyword"), it.get("single_mode"), parts_show,
                 status, ("  [" + why + "]") if why else ""))
        return status != "生效"

    print("全局层：%s（%d 条）" % (gpath or "（未配置）", len(gitems)))
    for it in gitems:
        dropped += _row(it)
    print("每源层：%s（%d 条；按 id 覆盖全局）" % (lp, len(items)))
    for it in items:
        dropped += _row(it)

    print("\n生效合并 id（%d 条）：%s" % (len(live_ids), sorted(live_ids)))
    print("\n提示：真实片段只以 <len=N> 呈现；占位符以 __XXX__ 呈现。")
    if dropped:
        print("警告：%d 条字面量**当前不会生效**（见上），请修正后重跑。" % dropped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())