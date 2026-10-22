# AI生成
"""上下文预览生成器：输出本地 HTML，供人工判断误报。

安全约束
- 输出文件含原文片段，因此默认写入 vault/preview/（本地加密区），不进 git、不进向量库。
- 绝不打印原文到 stdout / 聊天。

目录无关
- 源目录缺省读 <data-root>/workspace.json 的 source，可用 --root 覆盖。
- 输出缺省 <data-root>/vault/preview/preview-<ts>.html，可用 --out 指定（会造成泄露，慎改）。

用法
    python preview.py --data-root <数据根> [--root <源目录>] [--out <html路径>]
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import get_salt, iter_files, load_rules, read_text, find_hits  # noqa: E402
import paths  # noqa: E402

CTX = 60  # 命中点左右保留的字符数


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--root", help="源目录（只读）；缺省读 <data-root>/workspace.json 的 source")
    ap.add_argument("--out", default=None,
                    help="输出 HTML；缺省 <data-root>/vault/preview/preview-<ts>.html（含原文）")
    ap.add_argument("--rules", default=None, help="规则文件；缺省 <data-root>/config/rules.json")
    ap.add_argument("--include-runtime", action="store_true")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    source_root = args.root or paths.workspace_source(root)
    if not source_root:
        ap.error("缺源目录：给 --root，或在 workspace.json 配置 source（先跑 guide.py）")
    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = args.out or os.path.join(root, "vault", "preview", f"preview-{ts}.html")
    rules_path = args.rules or os.path.join(root, "config", "rules.json")

    rules = load_rules(rules_path)
    salt = get_salt(os.path.join(root, "vault", "map", "salt.bin"))

    rows: list[tuple[str, int, str, str, str]] = []  # file, line, label, snippet, token
    n = 0
    for path in iter_files(source_root, args.include_runtime, False):
        text = read_text(path)
        if text is None:
            continue
        hits = find_hits(text, rules, salt)
        if not hits:
            continue
        rel = os.path.relpath(path, source_root)
        byline = collections.defaultdict(list)
        for h in hits:
            byline[h.line].append(h)
        for line_no, hs in sorted(byline.items()):
            for h in hs:
                s = max(0, h.start - CTX)
                e = min(len(text), h.end + CTX)
                snip = html.escape(text[s:h.start]) + \
                    '<mark>' + html.escape(text[h.start:h.end]) + '</mark>' + \
                    html.escape(text[h.end:e])
                rows.append((rel, line_no, h.rule.label, snip, h.token))
                n += 1

    css = """
body{font:13px/1.6 -apple-system,Segoe UI,Microsoft YaHei,sans-serif;margin:0;padding:24px;background:#0f1115;color:#dfe3ea}
h1{font-size:18px;margin:0 0 4px}
.sub{color:#8b93a7;margin-bottom:18px}
table{border-collapse:collapse;width:100%}
th,td{border:1px solid #262b36;padding:6px 8px;vertical-align:top;text-align:left}
th{background:#171b23;position:sticky;top:0}
mark{background:#7c2d12;color:#ffd9c9;padding:1px 3px;border-radius:3px}
code{font-family:Consolas,monospace;font-size:12px;word-break:break-all}
.t{color:#6ee7b7;white-space:nowrap;font-family:Consolas,monospace}
.f{white-space:nowrap;color:#c7cbd6}
tr:nth-child(even){background:#141821}
"""

    parts = [
        "<!doctype html><meta charset='utf-8'>",
        f"<title>脱敏上下文预览 {len(rows)} 条</title>",
        f"<style>{css}</style>",
        "<h1>脱敏上下文预览（含原文，仅本地）</h1>",
        f"<div class='sub'>生成 {dt.datetime.now():%Y-%m-%d %H:%M:%S}　"
        f"根目录 {html.escape(source_root)}　命中 {len(rows)} 条　"
        "用途：人工判断误报。<b>本文件含原文，不得进入版本库或向量库。</b></div>",
        "<table><thead><tr><th>#</th><th>文件</th><th>行</th><th>类型</th>"
        "<th>上下文（红色为将被替换的部分）</th><th>替换为</th></tr></thead><tbody>",
    ]
    for i, (f, ln, label, snip, token) in enumerate(rows, 1):
        parts.append(
            f"<tr><td>{i}</td><td class='f'>{html.escape(f)}</td><td>{ln}</td>"
            f"<td><code>{label}</code></td><td><code>{snip}</code></td>"
            f"<td class='t'>{html.escape(token)}</td></tr>"
        )
    parts.append("</tbody></table>")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print(f"rows={len(rows)}")
    print("out:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())