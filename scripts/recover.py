# AI生成
"""recover.py — 用「离线备份 + 脱敏镜像」重建原始内容（把占位符还原成原文）。

背景（用户 2026-10-18 需求；引用标识 2026-10-23 定稿）
- 要求：**备份的内容 + sanitized 镜像，必须能够恢复出原始知识库。**
- 脱敏镜像里的凭据可能是两种形态：
    1) token 形态   [REDACTED:<label>:<hmac8>]                       —— sanitize 刚产出的样子
    2) 引用形态     见保险库：<条目名> [vault:<label>:<hmac8>]        —— backfill 之后的样子
- 两者都能还原，且**都只依赖 vault/map/map.json**：
    token  → 原文    取自 vault/map/map.json
    引用   → 原文    按引用里内嵌的 [vault:...] 主键查 map.json

为什么引用用 vault: 主键（2026-10-23 定稿）
- 旧式引用「见保险库：<条目名>」按**条目名**逆映射（经 bw-items.json），
  而条目名不唯一（同文件同标签的多个凭据同名）→ 重名处不可精确还原。
- 现引用为 `见保险库：<条目名> [vault:<label>:<hash8>]`：
  `<条目名>` 保留人可读、且与 Bitwarden 条目名逐字对应（便于网页端搜索）；
  `[vault:<label>:<hash8>]` 是唯一主键（= map.json 的 token 去掉 `REDACTED` 前缀）
  → 恢复**不依赖条目名唯一性**。
- 不复用 `[REDACTED:...]` 形态：那是 sanitize 的占位符，backfill 重跑会二次替换（不幂等），
  且容易与镜像里保留的 PII 占位符混淆。
- 因此恢复只需 map.json，与 bw-items.json / Bitwarden 是否可用无关。

安全
- 原文来自 vault/map/map.json（SECRET），本脚本**绝不打印任何原文**
  （只输出计数、文件路径与校验结果）。
- 默认 --verify（只读、dry-run）；--apply 才写盘。
- 默认重建到独立目录 <data-root>/recovered/<name>/，**不覆盖镜像**
  （镜像是「对外分享物」，不应被就地解密）。确需就地还原时才用 --in-place。

目录无关
- --data-root 定位 vault/ 与 workspace.json；--mirror 缺省读 workspace.json 的 mirror。

两种恢复模式
- **全量恢复**（默认）：整个工作副本 → 原文，重建出完整知识库。
- **单文件恢复**：`--file <镜像相对路径>`（可重复；给目录则展开其下文件），
  只还原指定文件，用于「某篇笔记要单独取回原文」。

用法
    # —— 全量恢复 ——
    # 1) 校验「备份 + 镜像」能否完整还原（不写盘；给了 --source 还会逐字节比对源）
    python scripts/recover.py --data-root <根> --verify
    python scripts/recover.py --data-root <根> --verify --source <源目录>
    # 2) 重建到独立目录
    python scripts/recover.py --data-root <根> --apply
    # 3) 就地还原镜像（谨慎：还原后镜像即含明文，勿再分享）
    python scripts/recover.py --data-root <根> --apply --in-place

    # —— 单文件恢复 ——
    python scripts/recover.py --data-root <根> --file pages/env-AI.md --verify
    python scripts/recover.py --data-root <根> --file pages/env-AI.md --apply
    python scripts/recover.py --data-root <根> --file journals/2024_01_01.md \
        --file pages/账号-X.md --apply --out <目标目录>
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sanitize_lib import (  # noqa: E402
    TOKEN_RX, VAULT_ID_RX, iter_files, read_text, is_text_file,
    GENERATED_DIRS, RUNTIME_DIRS, GENERATED_NAMES,
)
import paths  # noqa: E402

# 引用形态：见保险库：<条目名> [vault:<label>:<hash8>]，最后一个方括号是唯一主键
_REF_RX = re.compile(
    re.escape(paths.VAULT_REF) + r"[^\n]*?" + VAULT_ID_RX.pattern
)


def load_tokens(root: str) -> dict:
    p = os.path.join(paths.map_dir(root), "map.json")
    if not os.path.exists(p):
        print("找不到映射表：", p, "\n（恢复必须件之一；请从离线备份里取回 map.json）",
              file=sys.stderr)
        raise SystemExit(2)
    with open(p, encoding="utf-8") as fh:
        return json.load(fh).get("tokens", {})


def reconstruct(text: str, tokens: dict, stats: collections.Counter) -> str:
    """把引用形态与 token 形态都还原成原文。只统计计数，不打印原文。

    引用形态内嵌 [vault:<label>:<hash8>] 主键 → 直接查 map.json，**不依赖条目名唯一性**。
    """
    # 1) 引用形态：见保险库：<条目名> [vault:<label>:<hash8>]
    def sub_ref(m: re.Match) -> str:
        key = f"[REDACTED:{m.group(1)}:{m.group(2)}]"
        rec = tokens.get(key)
        v = rec.get("value") if rec else None
        if v is None:
            stats["ref_unresolved"] += 1
            return m.group(0)
        stats["ref_replaced"] += 1
        return v

    text = _REF_RX.sub(sub_ref, text)

    # 2) token 形态： [REDACTED:...]
    def sub_tok(m: re.Match) -> str:
        rec = tokens.get(m.group(0))
        v = rec.get("value") if rec else None
        if v is None:
            stats["token_unresolved"] += 1
            return m.group(0)
        stats["token_replaced"] += 1
        return v

    text = TOKEN_RX.sub(sub_tok, text)
    # 剩余「见保险库：」计数：内嵌主键未被解析时会残留 → 记为未还原
    stats["ref_leftover"] += text.count(paths.VAULT_REF)
    return text


def select_files(mirror: str, wanted: list[str]) -> tuple[list[str], list[str]]:
    """挑出要处理的文件；wanted 为空 = 全量。

    返回 (绝对路径列表, 未找到的相对路径列表)。给目录则展开其下的文件。
    """
    if not wanted:
        return list(iter_files(mirror)), []
    found: list[str] = []
    missing: list[str] = []
    seen: set[str] = set()
    for w in wanted:
        rel = os.path.normpath(w.strip().strip("/\\"))
        p = os.path.join(mirror, rel)
        if os.path.isdir(p):
            for f in iter_files(p):
                if f not in seen:
                    seen.add(f)
                    found.append(f)
        elif os.path.isfile(p):
            ap_ = os.path.abspath(p)
            if ap_ not in seen:
                seen.add(ap_)
                found.append(ap_)
        else:
            missing.append(rel)
    return found, missing


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--mirror", help="脱敏镜像根；缺省读 workspace.json 的 mirror")
    ap.add_argument("--source", help="只读源目录；用于逐字节校验（缺省读 workspace.json）")
    ap.add_argument("--out", help="重建输出目录；缺省 <data-root>/recovered/<name>")
    ap.add_argument("--file", action="append", default=[], metavar="REL",
                    help="单文件恢复：只还原该文件（镜像相对路径；可重复；给目录则展开其下文件）。"
                         "不带则全量恢复")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--verify", action="store_true", help="只校验、不写盘（默认行为）")
    g.add_argument("--apply", action="store_true", help="写盘")
    ap.add_argument("--in-place", action="store_true",
                    help="就地写回镜像（谨慎：还原后镜像即含明文，勿再分享）")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    mirror = args.mirror or paths.workspace_mirror(root)
    if not os.path.isdir(mirror):
        print(f"镜像目录不存在：{mirror}", file=sys.stderr)
        return 2
    source = args.source or paths.workspace_source(root)
    ws_name = paths.load_workspace(root).get("name") or "notes"
    outdir = args.out or os.path.join(root, "recovered", ws_name)

    files, missing = select_files(mirror, args.file)
    if missing:
        print("在镜像里找不到这些路径：", file=sys.stderr)
        for r in missing:
            print("  -", r, file=sys.stderr)
        return 2
    single = bool(args.file)

    tokens = load_tokens(root)

    stats: collections.Counter = collections.Counter()
    rows: list[tuple[str, str, str]] = []   # (rel, new_text, original_text)
    raw_copy: list[str] = []                # 按文本处理不了（二进制/非 utf-8）：原样拷回
    for p in files:
        text = read_text(p) if is_text_file(p) else None
        if text is None:
            if single:
                raw_copy.append(p)
            continue
        if not single and "REDACTED" not in text and paths.VAULT_REF not in text:
            continue
        new = reconstruct(text, tokens, stats)
        rel = os.path.relpath(p, mirror)
        rows.append((rel, new, text))
        if new != text:
            stats["files_changed"] += 1

    # 与源逐字节比对（若源可得）
    if source and os.path.isdir(source):
        for rel, new, _old in rows:
            sp = os.path.join(source, rel)
            src = read_text(sp)
            if src is None:
                stats["verify_no_source"] += 1
            elif src == new:
                stats["verify_match"] += 1
            else:
                stats["verify_mismatch"] += 1
                print("  ✗ 不一致：", rel, file=sys.stderr)

    print("镜像目录：", mirror)
    print("映射表　：", os.path.join(paths.map_dir(root), "map.json"))
    print("引用形态：", f"{paths.VAULT_REF}<条目名> [vault:<label>:<hash8>]"
                        "（按内嵌主键经 map.json 还原）")
    print()
    print(f"token 形态还原 : {stats['token_replaced']} 处"
          f"（未解析 {stats['token_unresolved']}）")
    print(f"引用形态还原   : {stats['ref_replaced']} 处"
          f"（未解析 {stats['ref_unresolved']}，残留 {stats['ref_leftover']}）")
    print(f"涉及文件       : {stats['files_changed']}")
    if source and os.path.isdir(source):
        print(f"源比对         : 一致 {stats['verify_match']} / "
              f"不一致 {stats['verify_mismatch']} / 源缺失 {stats['verify_no_source']}")

    unresolved = (stats["token_unresolved"] + stats["ref_unresolved"]
                  + stats["ref_leftover"])
    if unresolved:
        print("\n⚠️  存在未解析占位符：请确认 map.json 是否来自同一份备份，"
              "或该引用是否缺少 [vault:<label>:<hash8>] 主键。")

    if not args.apply:
        print("\n[校验模式] 未写盘。加 --apply 才重建。")
        return 0 if not unresolved else 1

    # 写盘
    for rel, new, old in rows:
        if args.in_place:
            dst = os.path.join(mirror, rel)
            content = new
        else:
            dst = os.path.join(outdir, rel)
            content = new
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)

    if not args.in_place:
        if single:
            # 单文件模式：请求里「按文本处理不了」的文件（二进制/非 utf-8）原样拷回
            for p in raw_copy:
                rel = os.path.relpath(p, mirror)
                dst = os.path.join(outdir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(p, dst)
            if raw_copy:
                print(f"\n另原样复制 {len(raw_copy)} 个非文本文件")
        else:
            # 全量模式：未命中占位符的其它文件也一并拷到独立目录，形成完整树
            copied = 0
            changed_rels = {r for r, _, _ in rows}
            for p in iter_files(mirror):
                rel = os.path.relpath(p, mirror)
                if rel in changed_rels:
                    continue
                text = read_text(p)
                if text is None:
                    continue
                dst = os.path.join(outdir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(dst, "w", encoding="utf-8", newline="") as fh:
                    fh.write(text)
                copied += 1
            # 二进制文件（脱敏从不改动）：原样拷回，保证恢复树与镜像一致
            copied_bin = 0
            for dirpath, dirnames, filenames in os.walk(mirror):
                dirnames[:] = [d for d in dirnames
                               if d not in GENERATED_DIRS and d not in RUNTIME_DIRS]
                for fn in filenames:
                    if fn in GENERATED_NAMES:
                        continue
                    p = os.path.join(dirpath, fn)
                    if is_text_file(p):
                        continue
                    rel = os.path.relpath(p, mirror)
                    if rel in changed_rels:
                        continue
                    dst = os.path.join(outdir, rel)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(p, dst)
                    copied_bin += 1
            print(f"\n另复制未含占位符的文本文件 {copied} 个、二进制文件 {copied_bin} 个")

    print("已重建 →", mirror if args.in_place else outdir)
    if args.in_place:
        print("⚠️  已就地还原镜像：该目录现在含明文，请勿再分享/入库。")
    return 0 if not unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())