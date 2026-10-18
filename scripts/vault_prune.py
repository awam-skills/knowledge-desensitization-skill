# AI生成
"""vault 产物剪枝：每个目录只保留最新的若干件，其余软移（同卷 move）到 superseded/。

只动「会累积的派生产物」，绝不触碰权威文件（salt.bin / map.json / age-key.txt / bw-items.json）。

用法：
    python scripts/vault_prune.py --data-root <数据根>           # 默认 dry-run，只列不搬
    python scripts/vault_prune.py --data-root <数据根> --apply   # 真正软移
"""
from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

# (相对 vault 的子目录, 通配, 保留件数)
TARGETS = [
    ("raw",     "*.zip.age",        1),
    ("map",     "bw-import-*.json", 1),
    ("preview", "preview-*.html",   1),
]

# 权威文件：永不触碰（仅用于断言提示）
PROTECTED = {"salt.bin", "map.json", "age-key.txt", "bw-items.json", "superseded"}


def main() -> int:
    ap = argparse.ArgumentParser()
    paths.add_root_arg(ap)
    ap.add_argument("--apply", action="store_true", help="真正执行软移（默认 dry-run）")
    args = ap.parse_args()

    vault = paths.vault_dir(paths.resolve_data_root(args.data_root))

    total_moved = 0
    for sub, pat, keep in TARGETS:
        d = os.path.join(vault, sub)
        if not os.path.isdir(d):
            print("[skip] 目录不存在：%s" % d)
            continue
        files = sorted(glob.glob(os.path.join(d, pat)),
                       key=os.path.getmtime, reverse=True)
        if len(files) <= keep:
            print("[ok] %s：%d 件 ≤ 保留 %d，无需处理"
                  % (os.path.relpath(d, vault), len(files), keep))
            continue
        keep_set = set(files[:keep])
        olds = [f for f in files if f not in keep_set]
        sup = os.path.join(d, "superseded")
        print("[%s] %s：保留 %d，软移 %d 件 → superseded/"
              % ("APPLY" if args.apply else "DRY", os.path.relpath(d, vault),
                 keep, len(olds)))
        for f in olds:
            base = os.path.basename(f)
            if base in PROTECTED:
                print("   !! 拒绝移动受保护文件：%s" % base)
                continue
            if args.apply:
                os.makedirs(sup, exist_ok=True)
                dst = os.path.join(sup, base)
                if os.path.exists(dst):  # 防覆盖
                    stem, ext = os.path.splitext(base)
                    dst = os.path.join(sup, "%s--dup%d%s" % (stem, len(os.listdir(sup)), ext))
                shutil.move(f, dst)  # 同卷 = rename，秒级
                ok = os.path.exists(dst) and not os.path.exists(f)
                print("   %s -> superseded/%s  [%s]" % (base, os.path.basename(dst),
                                                         "verified" if ok else "FAILED"))
            else:
                print("   (would move) %s" % base)
        total_moved += len(olds)

    if not args.apply:
        print("\nDRY-RUN：加 --apply 才会真正软移。")
    else:
        print("\n完成：共软移 %d 件（均在同卷 superseded/，可随时移回）。" % total_moved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())