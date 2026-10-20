# -*- coding: utf-8 -*-
"""断点续跑：记下"哪一步做完了、当时的产物是什么样"。

解决的是真问题：**中间产物会把幻觉传下去。**
改完分镜表之后，上一次 scan 的结论就作废了——但没人记得那是改之前扫的，
于是拿着旧结论去交付。进度文件用**内容指纹**而不是修改时间来判断：

    mtime 会被 checkout、复制、编辑器保存动作改掉，指纹变了才是内容真变了。

三条纪律：
  1. 只记事实，不记判断。"scan 时 0 阻断"是事实，"这稿不错"不是。
  2. 指纹用 sha256 前 12 位，够用且短——这不是安全用途。
  3. 缺文件不报错，报"缺失"。断点续跑的价值在于告诉你缺什么，不是拦住你。

用法：
    python engine/lib/progress.py <项目目录> [--json]
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

from common import C, CONST

NAME = ".progress.json"

# 阶段 → 该阶段正常会产出的文件（没记录时用来兜底判断"文件在不在"）
STAGES = (
    ("new", "立项", ("project.json", "README.md")),
    ("skel", "分镜骨架", ("分镜表.md", "第01集-分镜表.md")),
    ("scan", "质检", ("分镜表.md", "第01集-分镜表.md")),
)

_HINTS = {"（待填）", "（待填：谁在左/右·朝向）"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()[:12]


def load(proj: Path) -> dict:
    f = proj / NAME
    if not f.exists():
        return {"version": CONST["drama"]["meta"]["version"], "stages": {}}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        # 文件坏了当没有——不因为一个缓存文件拦住整条流程
        return {"version": CONST["drama"]["meta"]["version"], "stages": {}}
    if not isinstance(d, dict):
        return {"version": CONST["drama"]["meta"]["version"], "stages": {}}
    d.setdefault("stages", {})
    return d


def save(proj: Path, data: dict) -> Path:
    data["version"] = CONST["drama"]["meta"]["version"]
    f = proj / NAME
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return f


def record(proj: Path, stage: str, outputs, note: str = "") -> dict:
    """记一步做完。outputs 给 Path 列表，存在的才记指纹。"""
    data = load(proj)
    outs = {}
    for p in outputs:
        p = Path(p)
        if p.exists() and p.is_file():
            outs[p.name] = sha(p)
    data["stages"][stage] = {
        "done": datetime.now().isoformat(timespec="seconds"),
        "outputs": outs,
        "note": note,
    }
    save(proj, data)
    return data


# 没记录时的说法要分阶段——"文件在但不是本工具建的"这句话对"质检"毫无意义，
# 分镜表本来就在，只是还没扫过。
_UNRECORDED = {
    "new": "未记录（旧项目，不是本工具建的）",
    "skel": "未记录（骨架在，但没记是哪一步生成的）",
    "scan": "还没扫过",
}


def status(proj: Path) -> list[dict]:
    """逐阶段判状态。四种：完成 / 产物已变更 / 产物缺失 / 未记录 / 未做。"""
    data = load(proj)
    rec = data.get("stages") or {}
    rows = []
    for key, label, defaults in STAGES:
        entry = rec.get(key)
        row = {"stage": key, "label": label, "at": "", "note": "", "state": ""}
        if entry:
            row["at"] = entry.get("done", "")
            row["note"] = entry.get("note", "")
            outs = entry.get("outputs") or {}
            if not outs:
                row["state"] = "无产物记录"
            else:
                missing = [n for n in outs if not (proj / n).exists()]
                changed = [n for n, h in outs.items()
                           if (proj / n).exists() and sha(proj / n) != h]
                if missing:
                    row["state"] = f"产物缺失：{'、'.join(missing)}"
                elif changed:
                    row["state"] = f"产物已变更：{'、'.join(changed)}"
                else:
                    row["state"] = "完成"
        else:
            # 没记录：文件在就当"旧项目"，不在就当"没做"
            present = [n for n in defaults if (proj / n).exists()]
            row["state"] = (_UNRECORDED.get(key, "未记录") if present else "未做")
        rows.append(row)
    return rows


def next_step(proj: Path, rows: list[dict]) -> str:
    """给一句"接下来该干什么"。断点续跑的价值全在这一句上。"""
    by = {r["stage"]: r["state"] for r in rows}
    if by["new"].startswith("未做"):
        return "dramakit new <项目> \"一句话灵感\""
    if by["skel"].startswith("未做"):
        return "dramakit skel <项目> --write"
    if "变更" in by["scan"]:
        return "分镜表在上次质检后被改过——重扫：dramakit scan <项目>/分镜表.md --record"
    if by["scan"] != "完成":
        return "dramakit scan <项目>/分镜表.md --record"
    return "三步都齐了：dramakit check <项目>/<项目>.md 校验交付格式"


def render(rows: list[dict], name: str, hint: str) -> str:
    color = {"完成": C.ok, "未做": C.dim}
    out = [C.bold(f"\n  进度 · {name}"), "  " + "─" * 56]
    for r in rows:
        st = r["state"]
        if st == "完成":
            tag = C.ok("✓ 完成")
        elif st.startswith("未做") or st == "还没扫过":
            tag = C.dim("· " + (st if st == "还没扫过" else "未做"))
        elif st.startswith("未记录"):
            tag = C.warn("? 未记录")
        elif st.startswith("产物已变更"):
            tag = C.err("! 产物已变更")
        elif st.startswith("产物缺失"):
            tag = C.err("! 产物缺失")
        else:
            tag = C.err("! " + st)
        # 非"完成/未做"的状态要把原因写出来——只显示一个 "!" 等于没说。
        # "未记录"类的标签已经把那句话说完了，不再重复一遍。
        tail = ""
        if st != "完成" and not st.startswith(("未做", "未记录", "还没扫过")):
            tail = C.dim("  " + st)
        when = f"  {r['at']}" if r["at"] else ""
        note = C.dim(f"  {r['note']}") if r["note"] else ""
        out.append(f"  {r['label']:<6} {tag}{C.dim(when)}{note}{tail}")
    out.append("")
    out.append(C.cyan(f"  下一步  {hint}"))
    out.append("")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    pos = [a for a in args if not a.startswith("--")]
    if not pos:
        print("用法: python progress.py <项目目录> [--json]")
        return 1
    proj = Path(pos[0])
    if not proj.exists():
        print(C.err(f"项目目录不存在: {proj}"))
        return 1
    rows = status(proj)
    if "--json" in args:
        print(json.dumps({"stages": rows, "next": next_step(proj, rows)},
                         ensure_ascii=False, indent=2))
    else:
        print(render(rows, proj.name, next_step(proj, rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
