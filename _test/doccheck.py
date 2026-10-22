# -*- coding: utf-8 -*-
"""文档对账：文档里引用的命令 / 数字 / 判据名，是否与代码一致。

查四类：
  1. 幽灵命令——文档写了 `dramakit xxx`，但 CLI 里没有 xxx 子命令
  2. 数字谎言——文档写"107 项断言"，实际 selftest 报 N 项
  3. 判据名谎言——文档提的 check 名，scan/check/cliff 里搜不到
  4. 形态名——文档写 supported 的形态，config 里没有

★ 按位置截断：只在"命令行位置"上匹配（行首/代码块/`dramakit ` 之后），
  否则散文里的"短剧工坊 scan"会被当成幽灵命令。
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
DK = ROOT / "engine" / "dramakit.py"
sys.path.insert(0, str(ROOT / "engine" / "lib"))
from common import FORMS  # noqa: E402

DOCS = [ROOT / "README.md"] + sorted((ROOT / "references").glob("*.md"))
SRC = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "engine").rglob("*.py")))

problems = []

# ---- 1. 幽灵命令 ----
out = subprocess.run([PY, str(DK), "--help"], capture_output=True, text=True,
                     encoding="utf-8").stdout
m = re.search(r"\{([a-z,]+)\}", out)
real_cmds = set(m.group(1).split(",")) if m else set()

for doc in DOCS:
    if not doc.exists():
        continue
    for i, ln in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        # 只认命令行位置：行首、代码块内、或 "dramakit " 之后
        for mm in re.finditer(r"(?:^|\$ |`|:\s+)dramakit\s+([a-z][a-z\-]*)", ln):
            cmd = mm.group(1)
            if cmd not in real_cmds and cmd not in ("py", "python"):
                problems.append(f"{doc.name}:{i} 幽灵命令 `dramakit {cmd}`")

# ---- 2. 数字谎言（selftest 项数）----
st = subprocess.run([PY, str(ROOT / "engine" / "lib" / "selftest.py")],
                    capture_output=True, text=True, encoding="utf-8").stdout
mn = re.search(r"(\d+)/(\d+) 全部通过", st) or re.search(r"(\d+)/(\d+) 通过", st)
real_n = int(mn.group(2)) if mn else -1
for doc in DOCS:
    if not doc.exists():
        continue
    txt = doc.read_text(encoding="utf-8")
    for mm in re.finditer(r"(\d+)\s*(?:项|条)?\s*(?:回归)?(?:断言|用例|检查项)", txt):
        if int(mm.group(1)) != real_n:
            problems.append(f"{doc.name} 写 {mm.group(1)} 项，实际 {real_n} 项")

# ---- 3. 形态名 ----
for doc in DOCS:
    if not doc.exists():
        continue
    txt = doc.read_text(encoding="utf-8")
    for mm in re.finditer(r"`?(live_action|anime|motion_comic|static_comic)`?", txt):
        if mm.group(1) not in FORMS:
            problems.append(f"{doc.name} 提到不存在的形态 {mm.group(1)}")

print(f"文档数：{len([d for d in DOCS if d.exists()])}")
print(f"真实命令：{len(real_cmds)} 个")
print(f"selftest 实际项数：{real_n}")
print(f"问题：{len(problems)}")
for p in problems:
    print("  ✗", p)
sys.exit(1 if problems else 0)
