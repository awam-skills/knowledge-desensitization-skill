# AI生成
"""只读扫描器：统计敏感命中，输出清单报告。

安全约束
- 绝不打印 / 写出任何匹配到的原文，只输出类型、token、文件、行号。
- 不修改任何源文件。

目录无关
- 源目录缺省读 <data-root>/workspace.json 的 source，可用 --root 覆盖。

用法
    python scan.py --data-root <数据根> [--root <源目录>] [--out <报告目录>]
                   [--include-runtime] [--include-generated] [--include-disabled]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import (  # noqa: E402
    get_salt, iter_files, load_rules, read_text, find_hits,
    load_path_excludes, path_excluded,
)
import paths  # noqa: E402


def group_of(rel: str) -> str:
    parts = rel.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else "(root)"


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--root", help="源目录（只读）；缺省读 <data-root>/workspace.json 的 source")
    ap.add_argument("--out", default=None, help="报告目录；缺省 <data-root>/reports")
    ap.add_argument("--rules", default=None, help="规则文件；缺省 <data-root>/config/rules.json")
    ap.add_argument("--include-runtime", action="store_true")
    ap.add_argument("--include-generated", action="store_true")
    ap.add_argument("--include-disabled", action="store_true",
                    help="同时统计 enabled=false 的规则（只统计不替换）")
    ap.add_argument("--exclude-config", default=None,
                    help="整文件/整目录排除 glob；缺省 <data-root>/config/exclude.json")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    source_root = args.root or paths.default_source(root)
    if not source_root:
        ap.error("缺源目录：给 --root，或在 <data-root>/workspace.json 配置 source")
    report_dir = args.out or os.path.join(root, "reports")
    rules_path = args.rules or os.path.join(root, "config", "rules.json")
    exclude_path = args.exclude_config or os.path.join(root, "config", "exclude.json")

    rules = load_rules(rules_path)
    excludes = load_path_excludes(exclude_path)
    salt = get_salt(os.path.join(root, "vault", "map", "salt.bin"))
    only_enabled = not args.include_disabled

    by_rule = collections.Counter()
    by_group = collections.defaultdict(collections.Counter)
    files_hit = collections.defaultdict(lambda: collections.Counter())
    n_files = n_lines = 0
    skipped_decode = 0

    for path in iter_files(source_root, args.include_runtime, args.include_generated):
        rel = os.path.relpath(path, source_root)
        if path_excluded(rel, excludes):
            continue
        text = read_text(path)
        if text is None:
            skipped_decode += 1
            continue
        n_files += 1
        n_lines += text.count("\n") + 1
        hits = find_hits(text, rules, salt, only_enabled=only_enabled)
        if not hits:
            continue
        g = group_of(rel)
        for h in hits:
            by_rule[h.rule.name] += 1
            by_group[g][h.rule.name] += 1
            files_hit[rel][h.rule.name] += 1

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(report_dir, exist_ok=True)
    label = {r.name: r.label for r in rules}
    cred = {r.name: r.credential for r in rules}
    enabled = {r.name: r.enabled for r in rules}

    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "root": source_root,
        "files_scanned": n_files,
        "lines_scanned": n_lines,
        "files_skipped_decode_error": skipped_decode,
        "include_runtime": args.include_runtime,
        "include_generated": args.include_generated,
        "counts_by_rule": dict(by_rule),
        "counts_by_group": {k: dict(v) for k, v in by_group.items()},
        "files": {k: dict(v) for k, v in sorted(files_hit.items())},
    }
    json_path = os.path.join(report_dir, f"scan-{ts}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    lines = [
        "# 敏感信息扫描报告（只读，无原文）", "",
        f"- 生成时间：{payload['generated_at']}",
        f"- 扫描根目录：`{source_root}`",
        f"- 文件数：{n_files}　行数：{n_lines}　解码失败跳过：{skipped_decode}",
        f"- 包含 Agent 运行时目录：{args.include_runtime}",
        "", "## 按类型汇总", "",
        "| 规则 | 标签 | 类型 | 条数 | 涉及文件 | 已启用 |",
        "|---|---|---|---|---|---|",
    ]
    for name, cnt in by_rule.most_common():
        nf = sum(1 for f in files_hit if files_hit[f][name])
        kind = "凭据" if cred[name] else "PII"
        lines.append(f"| {name} | `{label[name]}` | {kind} | {cnt} | {nf} | "
                     f"{'是' if enabled[name] else '否（仅统计）'} |")
    lines += ["", "## 按目录分组", "", "| 分组 | 命中 | 明细 |", "|---|---|---|"]
    for g, c in sorted(by_group.items(), key=lambda x: -sum(x[1].values())):
        detail = "、".join(f"{label[k]}×{v}" for k, v in c.most_common())
        lines.append(f"| `{g}/` | {sum(c.values())} | {detail} |")
    lines += ["", "## 命中文件明细", "",
              "| 文件 | 命中 | 标签×条数 |", "|---|---|---|"]
    for f, c in sorted(files_hit.items(), key=lambda x: -sum(x[1].values())):
        detail = "、".join(f"{label[k]}×{v}" for k, v in c.most_common())
        lines.append(f"| `{f}` | {sum(c.values())} | {detail} |")
    lines += ["", "> 说明：token 与行号明细见同名 json。报告中不含任何原文。"]
    md_path = os.path.join(report_dir, f"scan-{ts}.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"files={n_files} lines={n_lines} decode_skipped={skipped_decode}")
    print("counts_by_rule:", json.dumps(dict(by_rule), ensure_ascii=False))
    print("counts_by_group:", json.dumps({k: dict(v) for k, v in by_group.items()},
                                         ensure_ascii=False))
    print("json:", json_path)
    print("md  :", md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())