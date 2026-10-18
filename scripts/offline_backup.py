# AI生成
"""offline_backup.py — 按敏感度分层，把备份拆成「可上云」与「必须离线」两个目录。

用户需求（2026-10-18）
- A+B（脱敏镜像 + 加密快照）放同一目录 `cloud/`，**该目录内不含任何其他文件**，
  可直接整目录上传网盘 / 私有 Git。
- C+D（密钥材料 + Bitwarden 映射）放同一目录 `offline/`，方便一次性手动拷贝到
  离线介质（U 盘 / 密码管理器）。
- reports 经评估**不进 cloud**：它虽无明文，但含「哪些文件含凭据」的索引，属敏感元数据，
  且对恢复无用；需要审计留存时放 `meta/reports/`（默认不打包，`--with-reports` 才含）。
- **默认「固定目录 + 增量更新」**（2026-10-18 追加）：`--dest` 即备份根，不再套日期子目录；
  重复执行只拷「新增 / 变化」的文件，**未变化的跳过**；源里已删除的旧文件**软移**到
  `superseded/<时间戳>/`（可恢复，不物理删除）。若仍想要「每次一个日期目录」，加 `--dated`。

产出结构（默认增量模式）
    <dest>/                        ← 固定备份根，反复执行原地增量更新
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
        superseded/<ts>/           ← 本次不再需要的旧文件（软移，可恢复）
        MANIFEST.json              ← 每个文件的 tier/字节/sha256（放根，cloud 保持干净）
        RESTORE.md                 ← 分层保存与恢复说明

Bitwarden 本体（D 级，不是文件、不在本目录）
    见 RESTORE.md「Bitwarden 本体备份方法」；用 `--bitwarden-export <文件>` 可把
    你自己在终端生成的**加密导出**一并放进 offline/bitwarden/。

安全
- 只输出「路径 + 字节数 + sha256 + 状态」，绝不打印任何文件内容（可能是明文）。
- 清理旧文件一律**软移**（移到 superseded/），绝不物理删除。

用法
    python scripts/offline_backup.py --data-root <根> --dest <目标目录> [--verify] \
        [--with-literals] [--with-reports] [--bitwarden-export <文件>] \
        [--tiers cloud,offline,meta] [--dated] [--prune-old]

    只把 C+D（离线层）备到某介质：
        python scripts/offline_backup.py --data-root <根> --dest <介质> \
            --tiers offline --with-literals --verify
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


def _norm(rel: str) -> str:
    """把相对路径规范成 posix 风格，便于跨平台比对。"""
    return rel.replace("\\", "/")


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


def load_prev_manifest(dest_root: str) -> dict:
    p = os.path.join(dest_root, "MANIFEST.json")
    if os.path.isfile(p):
        try:
            with open(p, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}
    return {}


def sync_file(src: str, dst: str) -> str:
    """增量拷贝：内容一致返回 'unchanged'（不写），否则拷并返回 'new'/'updated'。"""
    if os.path.isfile(dst):
        try:
            if os.path.getsize(dst) == os.path.getsize(src) and sha256(dst) == sha256(src):
                return "unchanged"
        except OSError:
            pass
        existed = True
    else:
        existed = False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return "updated" if existed else "new"


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--dest", required=True, help="备份目标根目录（可为 U 盘 / 同步盘）")
    ap.add_argument("--with-literals", action="store_true",
                    help="把 config/literals.json（含明文）纳入 offline/（目标介质须安全）")
    ap.add_argument("--with-reports", action="store_true",
                    help="把 reports/ 纳入 meta/（默认不打包：含敏感文件索引，不上云）")
    ap.add_argument("--bitwarden-export", metavar="FILE",
                    help="你在终端生成的 Bitwarden 加密导出文件，一并放入 offline/bitwarden/")
    ap.add_argument("--verify", action="store_true", help="拷后重算哈希核对")
    ap.add_argument("--dated", action="store_true",
                    help="（旧行为）在 --dest 下新建 <时间戳> 子目录，而非原地增量更新")
    ap.add_argument("--prune-old", action="store_true",
                    help="仅 --dated 模式：把目标下旧日期备份软移 superseded/，只留最新")
    ap.add_argument("--tiers", default="cloud,offline,meta",
                    help="只备份指定层（逗号分隔，可选 cloud/offline/meta 组合）；默认全部")
    args = ap.parse_args()

    tiers = {t.strip() for t in args.tiers.split(",") if t.strip()}
    unknown = tiers - {"cloud", "offline", "meta"}
    if unknown:
        print(f"--tiers 含未知层：{','.join(sorted(unknown))}（可选 cloud/offline/meta）",
              file=sys.stderr)
        return 2

    root = paths.resolve_data_root(args.data_root)
    dest_base = os.path.abspath(args.dest)
    if not os.path.isdir(dest_base):
        print(f"目标目录不存在：{dest_base}", file=sys.stderr)
        return 2

    ws = paths.load_workspace(root)
    name = ws.get("name") or "notes"
    mirror = paths.default_mirror(root)

    ts = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    # ---- 决定输出根：增量（固定） or 日期子目录 ----
    if args.dated:
        outdir = os.path.join(dest_base, f"knowledge-desensitization-backup-{ts}")
        os.makedirs(outdir, exist_ok=True)
        incremental = False
    else:
        outdir = dest_base
        incremental = True

    # ---- 先汇总「本次应存在的文件」清单 ----
    items: list[tuple[str, str, str]] = []  # (tier, rel_in_backup, src_abs)

    if "cloud" in tiers:
        if mirror and os.path.isdir(mirror):
            for dp, _dn, fns in os.walk(mirror):
                for fn in fns:
                    src = os.path.join(dp, fn)
                    rel = os.path.join("cloud", name, os.path.relpath(src, mirror))
                    items.append(("cloud", rel, src))
        else:
            print("  ⚠️  未找到脱敏镜像（cloud/ 将不含 A 层；恢复需要它）", file=sys.stderr)
        for a in archive_paths(root):
            items.append(("cloud", os.path.join("cloud", "archive", os.path.basename(a)), a))

    if "offline" in tiers:
        for rel in offline_rels(root, args.with_literals):
            items.append(("offline", os.path.join("offline", rel), os.path.join(root, rel)))
        if args.bitwarden_export:
            src = os.path.abspath(args.bitwarden_export)
            if os.path.isfile(src):
                items.append(("offline", os.path.join("offline", "bitwarden",
                                                      os.path.basename(src)), src))
            else:
                print(f"  ⚠️  --bitwarden-export 文件不存在：{src}", file=sys.stderr)

    if "meta" in tiers:
        for rel in meta_rels(root, args.with_reports):
            items.append(("meta", os.path.join("meta", rel), os.path.join(root, rel)))

    if not items:
        print("没有可备份的文件（vault/ 是否已初始化？）", file=sys.stderr)
        return 2

    # ---- 增量：读旧清单，算出「本次不再需要」的旧文件 ----
    prev = load_prev_manifest(outdir) if incremental else {}
    prev_rels = {_norm(f["path"]) for f in prev.get("files", []) if f.get("path")}
    desired_rels = {_norm(rel) for (_t, rel, _s) in items}
    stale = sorted(prev_rels - desired_rels)

    manifest = {"created": dt.datetime.now().isoformat(timespec="seconds"),
                "mode": "incremental" if incremental else "dated",
                "tiers": sorted(tiers),
                "data_root": root, "dest": outdir, "name": name, "files": []}
    total = 0
    stats = {"new": 0, "updated": 0, "unchanged": 0}
    prev_sha = {_norm(f["path"]): f.get("sha256")
                for f in prev.get("files", []) if f.get("path")}

    def record(tier: str, rel_in_backup: str, src_abs: str, status: str) -> None:
        nonlocal total
        size = os.path.getsize(src_abs)
        h = sha256(src_abs)
        if args.verify:
            ok = (sha256(os.path.join(outdir, rel_in_backup)) == h)
        else:
            ok = True
        manifest["files"].append({"tier": tier, "path": rel_in_backup,
                                  "bytes": size, "sha256": h,
                                  "status": status, "verified": ok})
        total += size
        stats[status] = stats.get(status, 0) + 1
        tag = {"new": "＋", "updated": "↻", "unchanged": "＝"}.get(status, "?")
        flag = "" if not args.verify else (" ✓" if ok else " ✗")
        print(f"  [{tier}] {tag} {rel_in_backup}  ({size} B){flag}")

    _hdr = {'cloud': "cloud/  ← A 脱敏镜像 + B 加密快照（可直接上传）",
            'offline': "offline/  ← C 密钥材料 + D Bitwarden 映射（手动拷到离线介质）",
            'meta': "meta/  ← 配置/审计（不上云）"}
    for _t in ("cloud", "offline", "meta"):
        if _t in tiers:
            print(_hdr[_t])
    if incremental:
        print("（增量模式：内容未变的文件将跳过）")
    for tier, rel, src_abs in items:
        # 增量模式下，若旧清单记录同哈希且目标存在，直接跳过哈希重算
        dst = os.path.join(outdir, rel)
        if incremental and os.path.isfile(dst):
            h_src = sha256(src_abs)
            if prev_sha.get(_norm(rel)) == h_src and os.path.getsize(dst) == os.path.getsize(src_abs):
                status = "unchanged"
            elif os.path.getsize(dst) == os.path.getsize(src_abs) and sha256(dst) == h_src:
                status = "unchanged"
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src_abs, dst)
                status = "updated"
        else:
            status = sync_file(src_abs, dst)
        record(tier, rel, src_abs, status)

    # ---- 软移「本次不再需要」的旧文件（增量模式）----
    moved = 0
    if stale:
        sup = os.path.join(outdir, "superseded", ts)
        for rel in stale:
            srcp = os.path.join(outdir, rel.replace("/", os.sep))
            if os.path.exists(srcp):
                tgt = os.path.join(sup, rel.replace("/", os.sep))
                os.makedirs(os.path.dirname(tgt), exist_ok=True)
                shutil.move(srcp, tgt)
                moved += 1
        if moved:
            print(f"  ↑ 软移 {moved} 个不再需要的旧文件 → superseded/{ts}/")

    # ---- 顺手把历史「日期目录」软移到 superseded/（迁移到固定目录布局）----
    if incremental:
        olds = sorted(glob.glob(os.path.join(
            dest_base, "knowledge-desensitization-backup-*")))
        for p in olds:
            if os.path.isdir(p):
                tgt = os.path.join(dest_base, "superseded", ts, os.path.basename(p))
                os.makedirs(os.path.dirname(tgt), exist_ok=True)
                if not os.path.exists(tgt):
                    shutil.move(p, tgt)
                    print(f"  软移历史日期备份 → superseded/{ts}/{os.path.basename(p)}")

    with open(os.path.join(outdir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    # ---- RESTORE.md ----
    counts = {}
    for f in manifest["files"]:
        counts[f["tier"]] = counts.get(f["tier"], 0) + 1
    restore_md = f"""# 分层备份 / 恢复说明

- 最近备份：{manifest['created']}　模式：**{'增量更新（原地）' if incremental else '日期快照'}**
- 源数据根：`{root}`　　数据集名：`{name}`
- 文件总数：{len(manifest['files'])}，合计 {total} 字节
  （cloud {counts.get('cloud',0)} / offline {counts.get('offline',0)} / meta {counts.get('meta',0)}）
- 本次：新增 {stats.get('new',0)} / 更新 {stats.get('updated',0)} / 未变 {stats.get('unchanged',0)}

> 增量模式：反复执行本命令即可原地刷新，只有新增/变化的文件会被写入；
> 源里删除的文件会**软移**到 `superseded/<时间戳>/`（不物理删除，可随时取回）。

## 目录含义与去路

| 目录 | 级别 | 内容 | 保存途径 |
|---|---|---|---|
| `cloud/` | A+B | `{name}/` 脱敏镜像、`archive/*.zip.age` 加密快照 | **直接整目录上传**网盘 / 私有 Git |
| `offline/` | C+D | `age-key.txt`、`salt.bin`、`map.json`、`bw-items.json`（+`literals.json`/`bitwarden/`） | **离线**：U 盘×2 / 密码管理器 / 保险柜；**禁止上云/入 Git** |
| `meta/` | — | 配置（rules/whitelist/exclude）；（可选）reports | 非密，可留存；**不与 cloud 混放** |
| `superseded/` | — | 历次被替换/删除的旧文件（软移） | 确认无用后可自行清理 |
| `MANIFEST.json` | — | 每个文件的 tier/sha256/status | 校验用 |

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
    print(f"\n已处理 {len(manifest['files'])} 个文件，合计 {total} 字节"
          f"（新增 {stats.get('new',0)} / 更新 {stats.get('updated',0)} / 未变 {stats.get('unchanged',0)}）")
    print("备份根：", outdir)
    if "cloud" in tiers:
        print("  ├─ cloud/    （可直接上传网盘）")
    if "offline" in tiers:
        print("  ├─ offline/  （手动拷到离线介质）")
    if "meta" in tiers:
        print("  ├─ meta/     （配置/审计，不上云）")
    print("  ├─ superseded/（软移的旧文件）" if incremental else "  ├─ (日期快照)")
    print("  ├─ MANIFEST.json")
    print("  └─ RESTORE.md")
    if args.verify:
        print("校验：", "全部通过 ✓" if not bad else f"有 {len(bad)} 个不匹配 ✗")
    if args.with_literals:
        print("⚠️  已包含 literals.json（含明文），请确保目标介质本身安全。")

    if args.dated and args.prune_old:
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