# AI生成
"""offline_backup.py — 按敏感度分层，把备份拆成「可上云」与「必须离线」两个目录。

用户需求（2026-10-18）
- A+B（脱敏镜像 + 加密快照）放同一目录 `cloud/`，**该目录内不含任何其他文件**，
  可直接整目录上传网盘 / 私有 Git。
- C+D（密钥材料 + Bitwarden 映射）放同一目录 `offline/`，方便一次性手动拷贝到
  离线介质（U 盘 / 密码管理器）。
- reports 经评估**不进 cloud**：它虽无明文，但含「哪些文件含凭据」的索引，属敏感元数据，
  且对恢复无用；需要审计留存时放 `meta/reports/`（默认不打包，`--with-reports` 才含）。

产出结构
    <dest>/knowledge-desensitization-backup-<ts>/
        cloud/                     ← 直接上传（只有 A+B）
            <name>/                 A: 脱敏镜像（可直接喂向量库）
            archive/                B: age 加密快照 *.zip.age（存在时）
        offline/                   ← 手动拷到离线介质（只有 C+D）
            vault/keys/age-key.txt
            vault/map/salt.bin
            vault/map/map.json
            vault/map/bw-items.json         （存在时）
            config/literals.json            （--with-literals；含明文）
            bitwarden/<export>.json         （--bitwarden-export；D 级导出副本）
        meta/                      ← 不上云、非密钥：配置与（可选）审计
            config/{rules,whitelist,exclude}.json
            reports/                （--with-reports 才含）
        MANIFEST.json              ← 每个文件的 tier/字节/sha256（放根，cloud 保持干净）
        RESTORE.md                 ← 分层保存与恢复说明

Bitwarden 本体（D 级，不是文件、不在本目录）
    见 RESTORE.md「Bitwarden 本体备份方法」；用 `--bitwarden-export <文件>` 可把
    你自己在终端生成的**加密导出**一并放进 offline/bitwarden/。

安全
- 只输出「路径 + 字节数 + sha256」，绝不打印任何文件内容（可能是明文）。

用法
    python scripts/offline_backup.py --data-root <根> --dest <目标目录> [--verify] \
        [--with-literals] [--with-reports] [--bitwarden-export <文件>] [--prune-old]
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
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


def _collect_under(root: str, rels: list[str]) -> list[str]:
    return [r for r in rels if os.path.exists(os.path.join(root, r))]


def offline_rels(root: str, with_literals: bool) -> list[str]:
    """C+D：必须离线的密钥材料。"""
    rels = [
        os.path.join("vault", "keys", "age-key.txt"),
        os.path.join("vault", "map", "salt.bin"),
        os.path.join("vault", "map", "map.json"),
        os.path.join("vault", "map", "bw-items.json"),
    ]
    if with_literals:
        rels.append(os.path.join("config", "literals.json"))
        for p in sorted(glob.glob(os.path.join(root, "config", "literals.json.bak-*"))):
            rels.append(os.path.relpath(p, root))
    return _collect_under(root, rels)


def meta_rels(root: str, with_reports: bool) -> list[str]:
    """非密但有用的辅助：配置（whitelist 不可再生）+（可选）审计报告。"""
    rels = [
        os.path.join("config", "rules.json"),
        os.path.join("config", "whitelist.json"),
        os.path.join("config", "exclude.json"),
    ]
    rels = _collect_under(root, rels)
    if with_reports:
        rdir = paths.reports_dir(root)
        for p in sorted(glob.glob(os.path.join(rdir, "**", "*"), recursive=True)):
            if os.path.isfile(p):
                rels.append(os.path.relpath(p, root))
    return rels


def archive_paths(root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(root, "vault", "raw", "*.zip.age")))


def copy_file(src: str, dst: str) -> int:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return os.path.getsize(src)


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--dest", required=True, help="备份目标目录（可为 U 盘 / 同步盘）")
    ap.add_argument("--with-literals", action="store_true",
                    help="把 config/literals.json（含明文）纳入 offline/（目标介质须安全）")
    ap.add_argument("--with-reports", action="store_true",
                    help="把 reports/ 纳入 meta/（默认不打包：含敏感文件索引，不上云）")
    ap.add_argument("--bitwarden-export", metavar="FILE",
                    help="你在终端生成的 Bitwarden 加密导出文件，一并放入 offline/bitwarden/")
    ap.add_argument("--verify", action="store_true", help="拷后重算哈希核对")
    ap.add_argument("--prune-old", action="store_true",
                    help="目标目录下旧备份软移 superseded/，只留最新")
    args = ap.parse_args()

    root = paths.resolve_data_root(args.data_root)
    dest_base = os.path.abspath(args.dest)
    if not os.path.isdir(dest_base):
        print(f"目标目录不存在：{dest_base}", file=sys.stderr)
        return 2

    ws = paths.load_workspace(root)
    name = ws.get("name") or "notes"
    mirror = paths.default_mirror(root)

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = os.path.join(dest_base, f"knowledge-desensitization-backup-{ts}")
    os.makedirs(outdir, exist_ok=True)

    manifest = {"created": dt.datetime.now().isoformat(timespec="seconds"),
                "data_root": root, "name": name, "files": []}
    total = 0

    def record(tier: str, rel_in_backup: str, src_abs: str) -> None:
        nonlocal total
        size = os.path.getsize(src_abs)
        h = sha256(src_abs)
        ok = True if not args.verify else (sha256(os.path.join(outdir, rel_in_backup)) == h)
        manifest["files"].append({"tier": tier, "path": rel_in_backup,
                                  "bytes": size, "sha256": h, "verified": ok})
        total += size
        flag = "" if not args.verify else (" ✓" if ok else " ✗")
        print(f"  [{tier}] {rel_in_backup}  ({size} B){flag}")

    # ---- cloud/ : A + B ----
    print("cloud/  ← A 脱敏镜像 + B 加密快照（可直接上传）")
    if mirror and os.path.isdir(mirror):
        dst = os.path.join(outdir, "cloud", name)
        shutil.copytree(mirror, dst, dirs_exist_ok=True)
        for dp, _dn, fns in os.walk(dst):
            for fn in fns:
                p = os.path.join(dp, fn)
                record("cloud", os.path.relpath(p, outdir), p)
    else:
        print("  ⚠️  未找到脱敏镜像（cloud/ 将不含 A 层；恢复需要它）", file=sys.stderr)
    for a in archive_paths(root):
        rel = os.path.join("cloud", "archive", os.path.basename(a))
        copy_file(a, os.path.join(outdir, rel))
        record("cloud", rel, a)

    # ---- offline/ : C + D ----
    print("offline/  ← C 密钥材料 + D Bitwarden 映射（手动拷到离线介质）")
    for rel in offline_rels(root, args.with_literals):
        copy_file(os.path.join(root, rel), os.path.join(outdir, "offline", rel))
        record("offline", os.path.join("offline", rel), os.path.join(root, rel))
    if args.bitwarden_export:
        src = os.path.abspath(args.bitwarden_export)
        if os.path.isfile(src):
            rel = os.path.join("offline", "bitwarden", os.path.basename(src))
            copy_file(src, os.path.join(outdir, rel))
            record("offline", rel, src)
        else:
            print(f"  ⚠️  --bitwarden-export 文件不存在：{src}", file=sys.stderr)

    # ---- meta/ : 配置（+可选报告）----
    print("meta/  ← 配置/审计（不上云）")
    for rel in meta_rels(root, args.with_reports):
        copy_file(os.path.join(root, rel), os.path.join(outdir, "meta", rel))
        record("meta", os.path.join("meta", rel), os.path.join(root, rel))

    if not manifest["files"]:
        print("没有可备份的文件（vault/ 是否已初始化？）", file=sys.stderr)
        return 2

    with open(os.path.join(outdir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    # ---- RESTORE.md ----
    counts = {}
    for f in manifest["files"]:
        counts[f["tier"]] = counts.get(f["tier"], 0) + 1
    restore_md = f"""# 离线备份 / 分层保存说明

- 备份时间：{manifest['created']}
- 源数据根：`{root}`　　数据集名：`{name}`
- 文件总数：{len(manifest['files'])}，合计 {total} 字节
  （cloud {counts.get('cloud',0)} / offline {counts.get('offline',0)} / meta {counts.get('meta',0)}）

## 目录含义与去路

| 目录 | 级别 | 内容 | 保存途径 |
|---|---|---|---|
| `cloud/` | A+B | `{name}/` 脱敏镜像、`archive/*.zip.age` 加密快照 | **直接整目录上传**网盘 / 私有 Git |
| `offline/` | C+D | `age-key.txt`、`salt.bin`、`map.json`、`bw-items.json`（+`literals.json`/`bitwarden/`） | **离线**：U 盘×2 / 密码管理器 / 保险柜；**禁止上云/入 Git** |
| `meta/` | — | 配置（rules/whitelist/exclude）；（可选）reports | 非密，可留存；**不与 cloud 混放** |
| `MANIFEST.json` | — | 每个文件的 tier/sha256 | 校验用 |

**核心原则：密文与钥匙分道。** `cloud/` 能公开（镜像已脱敏、快照已加密）；
`offline/` 一旦泄露 = 全盘明文。

## 恢复步骤（备份 + 脱敏镜像 → 原始知识库）

前置：`recover.py` 需要 `map.json`（offline 层）+ 脱敏镜像（cloud 层）。

1. 新建数据根：`python scripts/init_workspace.py --data-root <新根> --source <源> --name {name}`
2. 把 `offline/` 下的 `vault/`、`config/` 覆盖回 `<新根>/`（保持相对路径）。
3. 把 `cloud/{name}/` 放回 `<新根>/sanitized/{name}/`。
4. 校验：`python scripts/recover.py --data-root <新根> --verify [--source <源>]`
5. 重建：`python scripts/recover.py --data-root <新根> --apply` → `<新根>/recovered/{name}/`
6. 交叉验证（若有 `cloud/archive/*.zip.age`）：
   `age -d -i vault/keys/age-key.txt <file>.zip.age > notes.zip`
7. 对照 `MANIFEST.json` 的 sha256 核对完整性。

> ⚠️ 跨机陷阱：`workspace.json` 存的是**绝对路径**，换机后务必重跑 `init_workspace.py`
> 或给脚本显式 `--mirror <新根>/sanitized/{name}`。

## Bitwarden 本体备份方法（D 级，不是文件）

Bitwarden 保险库不在本目录。请**在你自己的终端**执行加密导出（需主密码，Agent 不代做）：

```
bw export --format encrypted_json --password "<导出口令>" --output bw-export.json
```

- `--format encrypted_json` = 加密导出（用你另设的导出口令保护），可安全存放；
- 想并入本备份：把生成的 `bw-export.json` 用 `--bitwarden-export <文件>` 重跑一次，
  它会落到 `offline/bitwarden/`（**仍属离线层，勿上云**）；
- **不要**用 `--format json`（明文导出）。

## 安全

- `offline/` 含秘密材料：禁止提交任何 Git 仓库、禁止上传公开位置。
- 若含 `--with-literals`，`offline/` 还含字面量明文，务必确保介质本身安全。
- `cloud/` 可上传，但请注意镜像里仍保留 PII 占位符（设计如此）。
"""
    with open(os.path.join(outdir, "RESTORE.md"), "w", encoding="utf-8") as fh:
        fh.write(restore_md)

    bad = [f for f in manifest["files"] if f.get("verified") is False]
    print(f"\n已备份 {len(manifest['files'])} 个文件，合计 {total} 字节")
    print("备份目录：", outdir)
    print("  ├─ cloud/    （可直接上传网盘）")
    print("  ├─ offline/  （手动拷到离线介质）")
    print("  ├─ meta/     （配置/审计，不上云）")
    print("  ├─ MANIFEST.json")
    print("  └─ RESTORE.md")
    if args.verify:
        print("校验：", "全部通过 ✓" if not bad else f"有 {len(bad)} 个不匹配 ✗")
    if args.with_literals:
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