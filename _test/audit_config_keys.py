# -*- coding: utf-8 -*-
"""审计：config/dramakit.toml 里每一个键，是否真的被 engine/ 代码消费。

方法：解析 TOML 拿到全量叶子键路径，逐个在 engine/ 源码里搜字面量。
搜不到 = 空转（表看起来在管事，实际没人读）。

这是本轮"表看起来在管事实际空转"那一类 bug 的机械化普查。
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "config" / "dramakit.toml"
ENGINE = ROOT / "engine"

KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 明知没有数据源、**故意**不接的键。空转和"无数据源"是两回事：
# 空转是"能查却不查"（是 bug），无数据源是"当前稿子里没有这一列，
# 机器查不到"（是声明）。后者必须由代码显式说出来，不能静默当通过。
# 这里每一项都必须附理由，且在代码里有对应的"需人工核对"提示。
DECLARED_NO_SOURCE = {
    "motion_comic.asset.expression_enum": "交付稿无「表情」列",
    "motion_comic.asset.costume_enum_required": "交付稿无「服装」列",
    "anime.asset.char_views": "参考图数量不在交付稿里，属制作端约束",
}


def leaf_paths(obj, prefix=""):
    out = []
    for k, v in obj.items():
        p = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.extend(leaf_paths(v, p))
        else:
            out.append(p)
    return out


def main() -> int:
    data = tomllib.loads(CFG.read_text(encoding="utf-8"))
    src = "\n".join(p.read_text(encoding="utf-8") for p in sorted(ENGINE.rglob("*.py")))

    # 源码里出现过的所有标识符串（去掉引号/括号后按 . 拆）
    tokens = set()
    for m in KEY_RE.finditer(src):
        tokens.add(m.group(0))

    paths = leaf_paths(data)
    dead = []
    for p in paths:
        last = p.split(".")[-1]
        if last in tokens:
            continue
        if p in DECLARED_NO_SOURCE:
            continue
        dead.append(p)

    print(f"config 叶子键总数：{len(paths)}")
    print(f"已声明无数据源（不算 bug）：{len(DECLARED_NO_SOURCE)}")
    print(f"真·空转键：{len(dead)}")
    if dead:
        print("\n--- 空转键（能查却不查）---")
        for p in dead:
            print("  ", p)
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())
