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


def record(proj: Path, stage: str, outputs, note: str = "",
           result: dict | None = None) -> dict:
    """记一步做完。outputs 给 Path 列表，存在的才记指纹。

    `result` 存这次动作的结构化结论（如 {"blocking": 2, "warning": 4}）。
    为什么要单列一个字段而不塞进 note：note 是给人看的，result 是给
    状态机看的——"扫过了"和"扫过了但还剩 2 个阻断"是两件事，
    混在一句文本里就得靠正则去抠，迟早抠错。
    """
    data = load(proj)
    outs = {}
    for p in outputs:
        p = Path(p)
        if p.exists() and p.is_file():
            outs[p.name] = sha(p)
    entry = {
        "done": datetime.now().isoformat(timespec="seconds"),
        "outputs": outs,
        "note": note,
    }
    if result:
        entry["result"] = result
    data["stages"][stage] = entry
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
    """逐阶段判状态。六种：完成 / 有阻断 / 产物已变更 / 产物缺失 / 未记录 / 未做。

    ★ "扫过了"不等于"通过了"。质检阶段带阻断时，状态必须是"有阻断"，
      不能显示"完成"——progress 的全部价值就是告诉人"现在能不能交付"，
      把带 2 个阻断的结果说成"完成"，等于让进度文件替你签了字。
      （实测：scan 退出码 1（有阻断），progress 却显示"质检 ✓ 完成"，
      next_step 接着说"三步都齐了"——这是最坏的一类假绿。）
    """
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
                    # 产物没变，但结论本身还带着阻断 → 不能叫"完成"
                    res = entry.get("result") or {}
                    nb = int(res.get("blocking") or 0)
                    if key == "scan" and nb > 0:
                        row["state"] = f"有阻断：{nb} 项未解决"
                    else:
                        row["state"] = "完成"
        else:
            # 没记录：文件在就当"旧项目"，不在就当"没做"
            present = [n for n in defaults if (proj / n).exists()]
            row["state"] = (_UNRECORDED.get(key, "未记录") if present
                            else f"未做（缺 {'/'.join(defaults[:1])}）")
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
    # 有阻断：先修阻断，别急着交付。绝不能走到"三步都齐了"那句——
    # 那是发给老板的定稿，不是带 2 个阻断的草稿。
    if by["scan"].startswith("有阻断"):
        return "质检还有阻断项未解决——先修：dramakit scan <项目>/分镜表.md"
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
        elif st.startswith("有阻断"):
            tag = C.err("✗ " + st)
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
        # 标签没把话说全的，把补充信息补在后面——只显示一个 "!" 等于没说。
        # 有括号就只补括号里的部分，避免"未记录 ? 未记录（…）"这种重复。
        tail = ""
        if st != "完成" and st != "还没扫过" and not st.startswith("有阻断"):
            tail = C.dim("  " + (st[st.index("（"):] if "（" in st else st))
        when = f"  {r['at']}" if r["at"] else ""
        note = C.dim(f"  {r['note']}") if r["note"] else ""
        out.append(f"  {r['label']:<6} {tag}{C.dim(when)}{note}{tail}")
    out.append("")
    out.append(C.cyan(f"  下一步  {hint}"))
    out.append("")
    return out and "\n".join(out)


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
