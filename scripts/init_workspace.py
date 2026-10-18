# AI生成
"""init_workspace.py — 初始化一个「数据工作区根」（data-root）。

技能包本身只含代码与模板，不含任何真实数据。数据工作区是运行期产物目录，
可以放在任意位置；本脚本负责一次性搭好它，并写入「隐私不入 Git」的护栏。

建出的结构：
    <data-root>/
      .desensitization-root        # 工作区标记（供脚本定位）
      workspace.json               # 记录 source / mirror / name
      .gitignore                   # 隐私护栏：vault/、literals.json 等永不入库
      config/
        rules.json                 # 从技能模板复制（已存在则不覆盖）
        whitelist.json             # 从模板生成（缺省空指纹）
        exclude.json               # 从模板生成
        literals.example.json      # 示例（可发布）
        literals.json              # 不创建；由用户在本机按示例自行填写
      vault/{keys,map,raw,preview}/
      reports/
      sanitized/<name>/

用法：
    python scripts/init_workspace.py --data-root <目录> --source <只读源目录> \\
        [--mirror <镜像目录>] [--name notes] [--force]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_CONFIG = os.path.join(SKILL_ROOT, "config")

GITIGNORE = """\
# === 隐私护栏（知识库脱敏技能自动生成）===
# 任何含明文/映射/密钥的目录与文件都不得进入 Git。

# 本地秘密区：映射表、salt、age 私钥、原始加密档、预览（含原文片段）
vault/

# 用户在本机填写的字面量清单（含真实片段）与其备份
config/literals.json
config/literals.json.bak-*
config/*.local.json

# 可能被导出的名单/残留表
mapping*.json
residual*.json
bw-import-*.json
bw-items.json
restore-log.jsonl

# 运行期派生
reports/
sanitized/
logs/
__pycache__/
*.pyc

# OS / 编辑器
.DS_Store
Thumbs.db
"""


def copy_if_absent(src: str, dst: str) -> bool:
    if os.path.exists(dst):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", required=True, metavar="DIR")
    ap.add_argument("--source", required=True, help="只读源目录（要脱敏的知识库）")
    ap.add_argument("--mirror", help="脱敏镜像目录；缺省 <data-root>/sanitized/<name>")
    ap.add_argument("--name", default="notes", help="数据集名（镜像子目录名）")
    ap.add_argument("--force", action="store_true",
                    help="已存在时也覆盖 workspace.json（不动 vault/ 与 literals.json）")
    args = ap.parse_args()

    root = os.path.abspath(args.data_root)
    src = os.path.abspath(args.source)
    name = args.name
    mirror = os.path.abspath(args.mirror) if args.mirror else os.path.join(
        root, "sanitized", name)

    if not os.path.isdir(src):
        print(f"源目录不存在：{src}", file=sys.stderr)
        return 2

    os.makedirs(root, exist_ok=True)

    # 1) 目录骨架
    for sub in ("config", "reports", "sanitized",
                os.path.join("vault", "keys"), os.path.join("vault", "map"),
                os.path.join("vault", "raw"), os.path.join("vault", "preview")):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    # 2) 工作区标记
    with open(os.path.join(root, paths.MARKER), "w", encoding="utf-8") as fh:
        fh.write("此目录是「知识库脱敏」数据工作区根。\n")

    # 3) workspace.json
    ws_path = paths.workspace_path(root)
    if os.path.exists(ws_path) and not args.force:
        print("workspace.json 已存在（未覆盖）。加 --force 可重写。")
    else:
        with open(ws_path, "w", encoding="utf-8") as fh:
            json.dump({"source": src, "mirror": mirror, "name": name},
                      fh, ensure_ascii=False, indent=2)
        print("已写入 workspace.json")

    # 4) .gitignore 护栏
    gi = os.path.join(root, ".gitignore")
    if os.path.exists(gi):
        with open(gi, encoding="utf-8") as fh:
            cur = fh.read()
        if "隐私护栏" not in cur:
            with open(gi, "a", encoding="utf-8") as fh:
                fh.write("\n" + GITIGNORE)
            print("已向既有 .gitignore 追加隐私护栏")
        else:
            print(".gitignore 已含隐私护栏（跳过）")
    else:
        with open(gi, "w", encoding="utf-8") as fh:
            fh.write(GITIGNORE)
        print("已写入 .gitignore")

    # 5) 从技能模板生成配置
    generated = []
    pairs = [
        ("rules.json", "rules.json"),               # 规则：直接复制
        ("whitelist.json", "whitelist.example.json"),
        ("exclude.json", "exclude.example.json"),
    ]
    for dst_name, src_name in pairs:
        s = os.path.join(SKILL_CONFIG, src_name)
        d = os.path.join(root, "config", dst_name)
        if not os.path.exists(s):
            continue
        if copy_if_absent(s, d):
            generated.append(dst_name)

    # literals.example.json 始终放一份（可发布模板）
    ex = os.path.join(SKILL_CONFIG, "literals.example.json")
    if os.path.exists(ex):
        copy_if_absent(ex, os.path.join(root, "config", "literals.example.json"))

    print("已生成配置:", ", ".join(generated) if generated else "（均已存在）")
    print("提示：真实片段请在本机把 config/literals.example.json 复制为 "
          "config/literals.json 后填写（该文件已在 .gitignore 中）。")

    print("\n数据工作区已就绪：", root)
    print("  源(只读) :", src)
    print("  镜像      :", mirror)
    print("  数据集名  :", name)
    print("\n下一步：")
    print(f"  python scripts/scan.py --data-root \"{root}\"")
    print(f"  python scripts/sanitize.py --data-root \"{root}\" --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())