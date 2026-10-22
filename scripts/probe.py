# AI生成
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""probe.py — 召回侦察器（L2 关键词邻域 + L3 高熵兜底）

目的：找出「现有规则没覆盖，但看起来是密钥」的候选，用于把漏报变成可度量。
安全：本脚本**绝不输出任何原文片段**。只输出 文件:行号 + 度量(长度/熵/字符类) + 指纹(sha256 前8位)。
     指纹可跨轮次识别同一个值，但不泄露内容。

目录无关：源目录缺省读 <data-root>/workspace.json 的 source，可用 --root 覆盖。
"""
import argparse
import collections
import datetime
import hashlib
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import (  # noqa: E402
    iter_files, read_text, load_path_excludes, path_excluded,
)
import paths  # noqa: E402

# 通用「值」切分：ASCII 可见字符里常见于密钥的那批
TOKEN_RE = re.compile(r"[A-Za-z0-9!@#$%^&*_\-+/.=]{8,}")

# 关键词：出现即认为该行「可能承载凭据」
KEYWORD_RE = re.compile(
    r"(密码|口令|密钥|凭据|账号|账户|通行码|授权码|私钥|证书|登录|"
    r"secret|token|api[\s_\-]?key|access[\s_\-]?key|apikey|appkey|app[\s_\-]?secret|"
    r"passwd|password|pwd|pass\b|sk-|ak[\s/]?sk|client[\s_\-]?(id|secret)|"
    r"bearer|authorization|cookie|webhook|dsn|conn|auth)",
    re.I,
)

# 明显不是密钥的英文词/常量
STOPWORDS = {
    "password", "passwd", "secret", "token", "apikey", "api_key", "accesskey",
    "access_key", "clientid", "client_id", "clientsecret", "client_secret",
    "username", "user_name", "administrator", "localhost", "https", "http",
    "example", "configuration", "application", "development", "production",
    "documentation", "international", "requirements", "environment",
    "false", "true", "none", "null", "undefined", "default", "string",
    "logseq", "github", "docker", "kubernetes", "javascript", "typescript",
    "webhook", "authorization", "bearer", "algorithms", "signature",
    "experience", "professional", "information", "management", "connection",
}

HIGH_ENTROPY = 4.0     # 兜底阈值
HIGH_ENTROPY_LEN = 16


def shannon(s: str) -> float:
    if not s:
        return 0.0
    cnt = collections.Counter(s)
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in cnt.values())


def classes(s: str) -> int:
    n = 0
    if re.search(r"[a-z]", s):
        n += 1
    if re.search(r"[A-Z]", s):
        n += 1
    if re.search(r"\d", s):
        n += 1
    if re.search(r"[^A-Za-z0-9]", s):
        n += 1
    return n


def fp(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:8]


def is_wordish(tok: str) -> bool:
    """纯字母且不长 → 大概率是英文词，不是密钥"""
    return tok.isalpha() and len(tok) < 24 and shannon(tok) < 3.9


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--root", help="源目录（只读）；缺省读 <data-root>/workspace.json 的 source")
    ap.add_argument("--out", default=None, help="报告输出目录；缺省 <data-root>/reports")
    ap.add_argument("--dirs", default="journals,pages",
                    help="只扫这些顶层目录，逗号分隔")
    ap.add_argument("--exclude-config", default=None,
                    help="整文件/整目录排除 glob；缺省 <data-root>/config/exclude.json")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    source_root = args.root or paths.workspace_source(root)
    if not source_root:
        ap.error("缺源目录：给 --root，或在 workspace.json 配置 source（先跑 guide.py）")
    report_dir = args.out or os.path.join(root, "reports")
    exclude_path = args.exclude_config or os.path.join(root, "config", "exclude.json")

    allow = {d.strip() for d in args.dirs.split(",") if d.strip()}
    excludes = load_path_excludes(exclude_path)
    os.makedirs(report_dir, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    kw_lines = []      # A 组：关键词行内的候选
    orphan = []        # B 组：无关键词但高熵
    kw_hit_count = collections.Counter()
    file_count = 0
    line_count = 0
    seen_fp = {}

    for path in iter_files(source_root, include_runtime=False, include_generated=False):
        rel = os.path.relpath(path, source_root)
        if path_excluded(rel, excludes):
            continue
        top = rel.split(os.sep)[0]
        if allow and top not in allow:
            continue
        if not rel.lower().endswith(".md"):
            continue
        file_count += 1
        text = read_text(path)
        if text is None:
            continue
        for i, line in enumerate(text.split("\n"), 1):
            line_count += 1
            kws = sorted({m.group(0).lower() for m in KEYWORD_RE.finditer(line)})
            toks = [t for t in TOKEN_RE.findall(line) if t.lower() not in STOPWORDS]
            toks = [t for t in toks if not is_wordish(t)]
            if kws:
                kw_hit_count.update(kws)
                for t in toks:
                    rec = (rel, i, t)
                    kw_lines.append(rec)
                    seen_fp.setdefault(fp(t), {"len": len(t), "ent": round(shannon(t), 2),
                                               "cls": classes(t)})
            else:
                for t in toks:
                    e = shannon(t)
                    if e >= HIGH_ENTROPY and len(t) >= HIGH_ENTROPY_LEN:
                        orphan.append((rel, i, t))
                        seen_fp.setdefault(fp(t), {"len": len(t), "ent": round(shannon(t), 2),
                                                   "cls": classes(t)})

    def dump(recs):
        out = []
        for rel, ln, t in recs:
            out.append({"file": rel, "line": ln, "fp": fp(t),
                        "len": len(t), "entropy": round(shannon(t), 2),
                        "classes": classes(t)})
        return out

    a = dump(kw_lines)
    b = dump(orphan)
    data = {
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "root": source_root,
        "dirs": sorted(allow),
        "files": file_count,
        "lines": line_count,
        "group_a_keyword_line_candidates": len(a),
        "group_b_orphan_high_entropy": len(b),
        "unique_fingerprints": len(seen_fp),
        "keyword_hits": dict(kw_hit_count.most_common()),
        "a": a,
        "b": b,
        "unique": {k: v for k, v in sorted(seen_fp.items())},
    }
    jp = os.path.join(report_dir, f"probe-{ts}.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # Markdown 摘要（同样不含原文）
    by_file_a = collections.Counter(x["file"] for x in a)
    by_file_b = collections.Counter(x["file"] for x in b)
    lines = [
        "# 召回侦察报告（掩码版，不含原文）", "",
        f"- 生成时间：{data['generated_at']}",
        f"- 范围：{', '.join(sorted(allow))}",
        f"- 扫描：{file_count} 文件 / {line_count} 行",
        f"- **A 组（关键词行内候选）：{len(a)} 处**",
        f"- **B 组（无关键词但高熵）：{len(b)} 处**",
        f"- 去重指纹：{len(seen_fp)} 个",
        "", "## 关键词热度", "",
        "| 关键词 | 行数 |", "|---|---|",
    ]
    for k, v in kw_hit_count.most_common(30):
        lines.append(f"| {k} | {v} |")
    lines += ["", "## A 组按文件", "", "| 文件 | 候选数 |", "|---|---|"]
    for k, v in by_file_a.most_common(40):
        lines.append(f"| {k} | {v} |")
    lines += ["", "## B 组按文件", "", "| 文件 | 候选数 |", "|---|---|"]
    for k, v in by_file_b.most_common(40):
        lines.append(f"| {k} | {v} |")
    lines += ["", "> 指纹 = sha256(值)[:8]，跨轮次可识别同一个值，不泄露内容。",
              "> 定位到具体文件行号后，请在本机打开原文核对。"]
    mp = os.path.join(report_dir, f"probe-{ts}.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"files={file_count} lines={line_count}")
    print(f"A(keyword-line)={len(a)}  B(orphan-high-entropy)={len(b)}  unique={len(seen_fp)}")
    print("json:", jp)
    print("md  :", mp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())