# AI生成
"""offline_backup.py — 把「恢复所必需」的文件一次性拷贝到用户指定目录（U 盘/加密网盘）。

丢失即不可逆的**必须三件套**：
    vault/keys/age-key.txt     age 私钥（解密原始归档）
    vault/map/salt.bin         HMAC salt（token 稳定性的根）
    vault/map/map.json         token ↔ 原文 映射

可选：
    vault/map/bw-items.json    导入映射（回填用）
    vault/raw/*.zip.age        原始加密归档        （--include-archives）
    config/literals.json       字面量清单（含明文！目标盘本身须安全）
    config/rules.json / whitelist.json / exclude.json
    config/*.bak-*             字面量备份

安全
- 只输出「路径 + 字节数 + sha256」，绝不打印任何文件内容（可能是明文）。
- --include-archives/--include-literals 默认关闭；开启时给出安全提醒。
- 拷贝后重算哈希核对，写 MANIFEST.json 与 RESTORE.md。

用法
    python scripts/offline_backup.py --data-root <根> --dest <目标目录> [--verify]
        [--include-archives] [--include-literals] [--prune-old]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(root: str, include_archives: bool, include_literals: bool) -> list[str]:
    items: list[str] = []
    must = [
        os.path.join("vault", "keys", "age-key.txt"),
        os.path.join("vault", "map", "salt.bin"),
        os.path.join("vault", "map", "map.json"),
    ]
    opt = [os.path.join("vault", "map", "bw-items.json"),
           os.path.join("config", "rules.json"),
           os.path.join("config", "whitelist.json"),
           os.path.join("config", "exclude.json")]
    for rel in must + opt:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            items.append(rel)
    if include_archives:
        for p in sorted(glob.glob(os.path.join(root, "vault", "raw", "*.zip.age"))):
            items.append(os.path.relpath(p, root))
    if include_literals:
        for pat in ("config/literals.json", "config/literals.json.bak-*"):
            for p in sorted(glob.glob(os.path.join(root, *pat.split("/")))):
                items.append(os.path.relpath(p, root))
    # 去重保序
    seen, out = set(), []
    for it in items:
        if it not in seen:
            seen.add(it)
            out.append(it)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--dest", required=True, help="备份目标目录（可为 U 盘）")
    ap.add_argument("--include-archives", action="store_true",
                    help="连同 vault/raw/*.zip.age 原始加密档一起备份")
    ap.add_argument("--include-literals", action="store_true",
                    help="连同 config/literals.json（含明文）一起备份（目标盘须安全）")
    ap.add_argument("--verify", action="store_true", help="拷后重算哈希核对")
    ap.add_argument("--prune-old", action="store_true",
                    help="目标目录下旧的同名备份软移，只留最新")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    dest_base = os.path.abspath(args.dest)
    if not os.path.isdir(dest_base):
        print(f"目标目录不存在：{dest_base}", file=sys.stderr)
        return 2

    rels = collect(root, args.include_archives, args.include_literals)
    if not rels:
        print("没有可备份的文件（vault/ 是否已初始化？）", file=sys.stderr)
        return 2

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(dest_base, f"knowledge-desensitization-backup-{ts}")
    os.makedirs(outdir, exist_ok=True)

    manifest = {"created": dt.datetime.now().isoformat(timespec="seconds"),
                "data_root": root, "files": []}
    total = 0
    for rel in rels:
        s = os.path.join(root, rel)
        d = os.path.join(outdir, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        size = os.path.getsize(s)
        h = sha256(s)
        ok = None
        if args.verify:
            ok = (sha256(d) == h)
        manifest["files"].append({"path": rel, "bytes": size,
                                  "sha256": h, "verified": ok})
        total += size
        flag = "" if ok is None else (" ✓" if ok else " ✗")
        print(f"  {rel}  ({size} B){flag}")

    with open(os.path.join(outdir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        import json
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    restore_md = f"""# 离线备份 / 恢复说明

- 备份时间：{manifest['created']}
- 源数据根：`{root}`
- 文件数：{len(manifest['files'])}，合计 {total} 字节

## 为什么这些文件重要

| 文件 | 丢失后果 |
|------|----------|
| `vault/keys/age-key.txt` | 无法解密 `vault/raw/*.zip.age` 原始归档 |
| `vault/map/salt.bin` | token 不再稳定，重跑会生成不同占位符 |
| `vault/map/map.json` | 无法把占位符还原成原文 |

**这三件套丢失 = 脱敏镜像不可逆。** 请务必离线保存（U 盘 / 加密网盘），
并与镜像分开存放。

## 恢复步骤

1. 在新机器的数据根创建 `vault/` 结构，把本备份的 `vault/` 与 `config/` 覆盖回去。
2. 用 age 私钥解密原始归档（若含 `vault/raw/*.zip.age`）：
   `age -d -i vault/keys/age-key.txt vault/raw/<file>.zip.age > notes.zip`
3. 校验完整性：对照本目录 `MANIFEST.json` 的 sha256。

## 安全

- 本目录含**秘密材料**，禁止提交到任何 Git 仓库、禁止上传到公开位置。
- 若勾选了 `--include-literals`，本目录还含字面量明文，请确保介质本身安全。
"""
    with open(os.path.join(outdir, "RESTORE.md"), "w", encoding="utf-8") as fh:
        fh.write(restore_md)

    bad = [f for f in manifest["files"] if f.get("verified") is False]
    print(f"\n已备份 {len(manifest['files'])} 个文件，合计 {total} 字节")
    print("目标目录：", outdir)
    if args.verify:
        print("校验：", "全部通过 ✓" if not bad else f"有 {len(bad)} 个不匹配 ✗")
    if args.include_literals:
        print("⚠️  已包含 literals.json（含明文），请确保目标介质本身安全。")

    if args.prune_old:
        olds = sorted(glob.glob(os.path.join(
            dest_base, "knowledge-desensitization-backup-*")))
        olds = [p for p in olds if os.path.abspath(p) != os.path.abspath(outdir)]
        if olds:
            superseded = os.path.join(dest_base, "superseded")
            os.makedirs(superseded, exist_ok=True)
            for p in olds:
                tgt = os.path.join(superseded, os.path.basename(p))
                if not os.path.exists(tgt):
                    shutil.move(p, tgt)
                    print("  软移旧备份 →", tgt)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())