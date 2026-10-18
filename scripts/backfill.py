# AI生成
"""backfill：把脱敏镜像里的凭据 token 换成「见保险库：xxx」引用。

依赖
    vault/map/bw-items.json   ← 由 scripts/bitwarden-import.ps1 生成
    其中每个条目的自定义字段 notes-token 记录了它对应的 [REDACTED:...] token。

行为
- 只处理 credential=true 的 token；PII token（id-cn / bank-card）保持原样。
- 只改 sanitized 镜像，不动源目录。
- 幂等：重复运行结果一致。

目录无关
- --mirror 缺省读 <data-root>/workspace.json 的 mirror。

用法
    python backfill.py --data-root <根> [--mirror <镜像>] [--apply]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import iter_files, load_rules, read_text  # noqa: E402
import paths  # noqa: E402

TOKEN_RX = re.compile(r"\[REDACTED:([a-z0-9\-]+):([0-9a-f]{8})\]")


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--mirror", help="脱敏镜像根；缺省读 workspace.json 的 mirror")
    ap.add_argument("--items", default=None,
                    help="bw-items.json；缺省 <data-root>/vault/map/bw-items.json")
    ap.add_argument("--rules", default=None,
                    help="规则文件；缺省 <data-root>/config/rules.json")
    ap.add_argument("--format", default=paths.VAULT_REF + "{name}")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    mirror = args.mirror or paths.default_mirror(root)
    if not mirror:
        print("缺镜像目录：给 --mirror，或在 <data-root>/workspace.json 配置 mirror",
              file=sys.stderr)
        return 2
    items_path = args.items or os.path.join(paths.map_dir(root), "bw-items.json")
    rules_path = args.rules or os.path.join(paths.config_dir(root), "rules.json")

    if not os.path.exists(items_path):
        print(f"未找到 {items_path}\n请先在你自己终端运行 scripts\\bitwarden-import.ps1",
              file=sys.stderr)
        return 2

    with open(items_path, encoding="utf-8") as fh:
        items = json.load(fh)
    if isinstance(items, dict):
        items = items.get("items", [])

    cred_labels = {r.label for r in load_rules(rules_path) if r.credential}

    mapping: dict[str, str] = {}
    for it in items:
        tok = None
        for f in (it.get("fields") or []):
            if f.get("name") == paths.TOKEN_FIELD:
                tok = f.get("value")
                break
        if not tok:
            continue
        name = (it.get("name") or "")
        if name.startswith(paths.ITEM_PREFIX + "/"):
            name = name[len(paths.ITEM_PREFIX) + 1:]
        name = name or it.get("id", "")
        mapping[tok] = args.format.format(name=name, id=it.get("id", ""))

    stats = collections.Counter()
    changed: list[tuple[str, str]] = []
    for p in iter_files(mirror):
        text = read_text(p)
        if text is None or "REDACTED" not in text:
            continue

        def sub(m: re.Match) -> str:
            label = m.group(1)
            if label not in cred_labels:
                stats["pii_kept"] += 1
                return m.group(0)
            ref = mapping.get(m.group(0))
            if not ref:
                stats["unmapped"] += 1
                return m.group(0)
            stats["replaced"] += 1
            return ref

        new = TOKEN_RX.sub(sub, text)
        if new != text:
            changed.append((p, new))

    print(f"可映射条目：{len(mapping)}")
    print(f"替换 {stats['replaced']} 处 / 未映射 {stats['unmapped']} 处 / PII 保留 {stats['pii_kept']} 处")
    print(f"涉及文件：{len(changed)}")

    if not args.apply:
        for p, _ in changed[:20]:
            print("  [dry-run]", os.path.relpath(p, mirror))
        print("\n[未写盘] 加 --apply 执行")
        return 0

    for p, new in changed:
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    rep = os.path.join(paths.reports_dir(root), f"backfill-{ts}.md")
    os.makedirs(os.path.dirname(rep), exist_ok=True)
    with open(rep, "w", encoding="utf-8") as fh:
        fh.write(f"# 凭据引用回填报告\n\n- 时间：{dt.datetime.now():%Y-%m-%d %H:%M:%S}\n"
                 f"- 替换：{stats['replaced']} 处\n- 未映射：{stats['unmapped']} 处\n"
                 f"- PII 保留：{stats['pii_kept']} 处\n- 文件：{len(changed)}\n\n")
        for p, _ in changed:
            fh.write(f"- `{os.path.relpath(p, mirror)}`\n")
    print("report:", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())