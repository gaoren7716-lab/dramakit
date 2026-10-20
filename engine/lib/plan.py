# -*- coding: utf-8 -*-
"""结构层：把"要讲什么"变成可检查的骨架。

短剧的结构不是三幕，是**四段**——处境、压力、反转、余味。四段的时长配比
写在配置里，代码只负责按比例分配并检查偏差。

系列连载是另一种结构：单集自洽 + 主线推进，两条线同时跑。分集表是系列的
核心产物——没有它，写到第十集就不知道前面埋了什么。

本模块只产出骨架，不写台词。台词和镜头交给人或模型。
"""
from __future__ import annotations

from common import CONST, C, platform_for

# 单集四段：名字 / 功能 / 必须发生的事 / 结尾状态
ACTS = [
    ("处境", "让观众在三秒内知道谁在什么地方、有什么不对劲",
     "一个具体的人在一个具体的空间里，做一件日常的事；其中有个细节不太对",
     "观众记住了一个人，和一个不对劲的东西"),
    ("压力", "外部事件压进来，主角必须做选择",
     "一件与主角相关的事发生了（不是内心戏，是外部事件）；主角做了一个有代价的选择",
     "局面比开头更糟，主角已经无法退回"),
    ("反转", "信息差揭穿，观众与主角的认知对调",
     "此前藏住的那一半信息露出来；同一句话或同一个动作，含义整个翻过来",
     "观众重新理解前面所有内容，主角的位置变了"),
    ("余味", "后果落地，用一个动作收尾",
     "不解释、不总结；只留一个身体动作或一个物件的变化",
     "观众带走一个画面，不是一个道理"),
]

# 钩子类型：每段结尾挂哪一种
ACT_HOOKS = ["不对劲", "逼到眼前", "认知翻转", "留白"]



def _sec(v) -> float:
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return 0.0


def beats(const: dict | None = None, total_s: float | None = None) -> list[dict]:
    """按四段配比切时长 → 节拍表。

    配比之和不必等于 1，代码按比例归一化——这样作者改一个数不用手动配平。
    """
    d = (const or CONST)["drama"]
    s = d["single"]
    total = _sec(total_s if total_s is not None else s["target_duration_s"])
    ratios = [float(r) for r in s["act_ratios"]]
    k = sum(ratios) or 1.0
    out, acc = [], 0.0
    for i, (name, goal, must, end) in enumerate(ACTS):
        share = total * ratios[i] / k
        # 最后一段吃掉累计误差，保证逐段相加严格等于 total
        dur = round(total - acc, 1) if i == len(ACTS) - 1 else round(share, 1)
        acc = round(acc + dur, 1)
        out.append({
            "no": i + 1, "name": name, "sec": dur,
            "pct": round(dur / total * 100) if total else 0,
            "goal": goal, "must": must, "end": end, "hook": ACT_HOOKS[i],
        })
    return out


def render_beats(const: dict | None = None, total_s: float | None = None,
                 platform: str | None = None) -> str:
    c = const or CONST
    d = c["drama"]
    rows = beats(const, total_s)
    total = sum(r["sec"] for r in rows)
    out = [C.bold(f"\n  单集节拍 · 目标 {total:.0f}s"), "  " + "─" * 56]
    for r in rows:
        pct = f"{r['pct']:>2}%"
        out.append(f"  {r['no']}  {C.bold(r['name'])}  {r['sec']:>5.1f}s  {C.dim(pct)}")
        out.append(C.dim(f"     功能  {r['goal']}"))
        out.append(f"     必须  {r['must']}")
        out.append(f"     收在  {r['end']}")
        out.append(C.dim(f"     钩子  {r['hook']}"))
        out.append("")
    lo = d["single"]["shots_min"]
    hi = d["single"]["shots_max"]
    out.append(C.dim(f"  镜数区间 {lo}-{hi}（{d['shot']['core_sec_min']}-{d['shot']['core_sec_max']}s 核心镜为主）"))
    spec = platform_for(c, platform)
    band = spec.get("duration_band") or []
    if band:
        ok = band[0] <= total <= band[1]
        tag = C.ok("✓ 在档") if ok else C.err("✗ 不在档")
        out.append(C.dim(f"  平台 {spec.get('note', '')}  {spec.get('aspect', '')}"
                         f"  {band[0]:.0f}-{band[1]:.0f}s  {tag}"))
    return "\n".join(out)



# ------------------------------------------------------------
# 系列：分集表
# ------------------------------------------------------------

def episode_rows(n: int, const: dict | None = None) -> list[dict]:
    """生成 n 集的分集表骨架，标出卡点与高潮位置。

    三条硬规矩落在这里：前 free_episodes 集钩子最密；paywall_episode 卡在
    "答案揭晓前一秒"；每 minor/major_climax_every 集各有一个高潮。
    """
    d = (const or CONST)["drama"]
    s = d["series"]
    free = int(s["free_episodes"])
    pay = int(s["paywall_episode"])
    minor = int(s["minor_climax_every"])
    major = int(s["major_climax_every"])
    rows = []
    for i in range(1, n + 1):
        marks = []
        if i <= free:
            marks.append("免费区·钩子加密")
        if i == pay:
            marks.append("★ 付费卡点")
        if major and i % major == 0:
            marks.append("大高潮")
        elif minor and i % minor == 0:
            marks.append("小高潮")
        rows.append({
            "no": i,
            "sec": int(s["episode_target_s"]),
            "unit": "",          # 本集自洽的小故事，填一句话
            "main": "",          # 主线推进，填一句话
            "hook": "",          # 结尾钩子，必须是画面或台词，不能是旁白
            "mark": " / ".join(marks),
        })
    return rows


def render_episodes(n: int | None = None, const: dict | None = None) -> str:
    d = (const or CONST)["drama"]
    n = int(n or d["series"]["episodes_default"])
    rows = episode_rows(n, const)
    out = [C.bold(f"\n  系列分集表 · {n} 集 × {d['series']['episode_target_s']}s"), "  " + "─" * 56,
           C.dim("  集  时长  单元线（本集自洽）        主线推进        结尾钩子        标记")]
    for r in rows:
        mark = C.warn(r["mark"]) if r["mark"] else ""
        out.append(f"  {r['no']:>2}  {r['sec']:>3}s  {'（待填）':<20}  {'（待填）':<12}  {'（待填）':<12}  {mark}")
    out.append("")
    out.append(C.dim("  每集必须有结尾钩子；单元线本集结清，主线只推进一步。"))
    return "\n".join(out)


# ------------------------------------------------------------
# Markdown 产物
# ------------------------------------------------------------

def beats_md(const: dict | None = None, total_s: float | None = None) -> str:
    rows = beats(const, total_s)
    total = sum(r["sec"] for r in rows)
    lines = [f"## 节拍表（{total:.0f} 秒 · 四段）", "",
             "| 段 | 时长 | 功能 | 必须发生的事 | 收在 | 钩子 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['no']} {r['name']} | {r['sec']:.0f}s | {r['goal']} | {r['must']} | {r['end']} | {r['hook']} |")
    return "\n".join(lines)


def episodes_md(n: int | None = None, const: dict | None = None) -> str:
    d = (const or CONST)["drama"]
    n = int(n or d["series"]["episodes_default"])
    lines = [f"## 分集表（{n} 集 × {d['series']['episode_target_s']}s）", "",
             "| 集 | 时长 | 单元线 | 主线推进 | 结尾钩子 | 标记 |",
             "|---|---|---|---|---|---|"]
    for r in episode_rows(n, const):
        lines.append(f"| {r['no']} | {r['sec']}s |  |  |  | {r['mark']} |")
    return "\n".join(lines)
