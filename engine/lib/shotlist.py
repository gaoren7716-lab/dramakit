# -*- coding: utf-8 -*-
"""分镜层：把节拍变成可拍的镜头表，并守住交付格式。

两件事：
1. **生成骨架** —— 按节拍时长和核心镜长，算出每段该给几镜，排出空表。
   这不是"替人写分镜"，是"把镜数配比和时长预算先算对"——人只要填动作和台词。
2. **校验交付** —— 定稿必须是一个 .md，五块结构、六列表头。少一块就不算定稿。

交付格式是硬约束，不是建议。老板被打回过三次，都栽在格式上。
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from common import (CONST, C, apply_form_profile, compliance_for, constants_for,
                    normalize_form, platform_for)
from plan import ACTS, beats

# 交付定稿的五块
BLOCKS = ["基本信息", "人物表", "场景", "分镜表", "验收额度"]
COLUMNS = ["镜", "时长", "景别", "谁在画面", "动作一句话", "台词原文"]
# 漫画/动漫向（有"格"的形态）的交付列头：把"动作一句话"换成"画面一句话"，
# "台词原文"换成"气泡对白"——不是改名游戏，是两类交付物确实不同：
# 视频交付要的是"能演的动作"，漫画交付要的是"能画的画面 + 装得下的气泡"。
COMIC_COLUMNS = ["镜", "时长", "景别", "谁在画面", "画面一句话", "气泡对白"]

# 有"格"的形态（交付用漫画列头）。**只有两种漫**——动漫短剧是连续动画，
# 交付仍是视频列头（要的是"能演的动作"与"台词原文"，不是"气泡对白"）。
PANEL_FORMS = ("motion_comic", "static_comic")

SIZES = ("大特写", "特写", "近景", "中景", "中全景", "全景", "远景")


def delivery_columns(const: dict | None = None, form: str | None = None) -> list:
    """本次交付该用哪套列头：有"格"的形态用漫画列头，其余用视频列头。"""
    d = (const or CONST)["drama"]
    frm = normalize_form(form or d.get("form") or "live_action")
    if frm in PANEL_FORMS:
        return list(d.get("delivery", {}).get("comic_columns") or COMIC_COLUMNS)
    return list(d.get("delivery", {}).get("shot_columns") or COLUMNS)



def _core(const: dict) -> float:
    s = const["drama"]["shot"]
    return (float(s["core_sec_min"]) + float(s["core_sec_max"])) / 2


def skeleton(const: dict | None = None, total_s: float | None = None,
             per_act: list[int] | None = None) -> list[dict]:
    """按节拍生成空镜表。

    per_act 可显式指定每段镜数；不给就按"核心镜长"折算。
    折算后不足配置下限的，补到下限——宁可多留一镜，也不要长镜硬撑。
    """
    const = const or CONST
    d = const["drama"]
    rows_beats = beats(const, total_s)
    core = _core(const)
    if not per_act:
        per_act = [max(1, round(b["sec"] / core)) for b in rows_beats]

    # 有"格"的形态默认景别用"中格"，视频形态用"中景"——
    # 骨架阶段就把名词写对，省得填表时被白名单警告纠正。
    frm = normalize_form(d.get("form"))
    default_size = "中格" if frm in PANEL_FORMS else "中景"

    rows, no = [], 0
    for b, n in zip(rows_beats, per_act):
        step = round(b["sec"] / n, 1) if n else b["sec"]
        for k in range(n):
            no += 1
            rows.append({
                "no": no, "act": b["name"], "sec": step,
                "size": default_size, "who": "", "action": "", "line": "",
            })
    return rows


def render_md(rows: list[dict], title: str = "", const: dict | None = None,
              form: str | None = None) -> str:
    cols = delivery_columns(const, form or ((const or CONST)["drama"].get("form")))
    out = []
    if title:
        out.append(f"## 分镜表 · {title}")
        out.append("")
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        out.append("| {} | {}s | {} | {} | {} | {} |".format(
            r["no"], r["sec"], r["size"], r["who"] or "（待填）",
            r["action"] or "（待填）", r["line"] or "—"))
    return "\n".join(out)


def to_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow([r["no"], f"{r['sec']}s", r["size"], r["who"], r["action"], r["line"]])
    return buf.getvalue()


# ------------------------------------------------------------
# 交付校验
# ------------------------------------------------------------

def check_delivery(text: str, const: dict | None = None,
                   form: str | None = None, is_ai: bool = True) -> dict:
    """检查定稿是否满足交付格式。

    查四样：五块标题齐不齐、分镜表六列表头对不对、验收额度有没有落数、
    AIGC 标识在不在（AI 生成内容必查）。这四样是"发出去之前"的最后一道，
    缺一样就别发。

    列头分两套：视频交付（动作一句话 / 台词原文）与漫画交付（画面一句话 /
    气泡对白）。**按形态取，不是二选一**——写错列头等于交错了东西。
    """
    const = const or CONST
    d = const["drama"]
    # ★ 交付校验也必须合形态：形态专属的资产登记要求（scene_bible_required /
    #   color_sheet_required）写在 <form>.asset 下，直接读全局 CONST 是取不到的。
    #   （实测：漫画交付的场景登记检查因为这条恒不触发，等于没有。）
    if "form" not in d:
        d = apply_form_profile({"drama": d},
                               normalize_form(form or "live_action"), "ai")["drama"]
    cols = delivery_columns({"drama": d}, form)
    frm = normalize_form(form or d.get("form") or "live_action")
    # 标题允许带序号（"## 一、基本信息"），也允许不带（"## 基本信息"）
    def _has_block(name: str) -> bool:
        pat = rf"^#+\s*(?:[一二三四五六七八九十]+\s*[、.．]\s*)?{re.escape(name)}"
        return bool(re.search(pat, text, re.M))

    missing = [b for b in BLOCKS if not _has_block(b)]

    head = ""
    m = re.search(r"^\|.*\|\s*$", text, re.M)
    for line in text.splitlines():
        if line.startswith("|") and "镜" in line and "时长" in line:
            head = line
            break
    got = [c.strip() for c in head.strip("|").split("|")] if head else []
    bad_cols = [c for c in cols if c not in got]

    # 验收额度必须落在"每镜几次 + 失败怎么办"这两件事上。
    # 用词分两种：AI 生成叫"重抽"，真人实拍叫"拍几条"，两种都认。
    quota_text = ""
    mq = re.search(r"^#+\s*(?:[一二三四五六七八九十]+\s*[、.．]\s*)?验收额度\s*$(.*?)(?=^#|\Z)",
                   text, re.M | re.S)
    if mq:
        quota_text = mq.group(1)
    quota = bool(re.search(r"\d", quota_text)) and bool(
        re.search(r"重抽|重拍|重试|拍\s*\d|条|次|不过|失败|停", quota_text))

    # AIGC 标识：AI 生成必带，真人实拍可关
    comp = compliance_for(const)
    aigc_missing = False
    if comp.get("aigc_label_required") and is_ai:
        if "AI 生成" not in text and "人工智能" not in text and "AI生成" not in text:
            aigc_missing = True

    # 资产登记（有"格"的形态）：场景表必须有条目，防跨镜场景漂移无人发现
    asset_block = d.get("asset") or {}
    asset_missing = False
    if asset_block.get("scene_bible_required") and frm in PANEL_FORMS:
        msc = re.search(r"^#+\s*(?:[一二三四五六七八九十]+\s*[、.．]\s*)?场景\s*$(.*?)(?=^#|\Z)",
                        text, re.M | re.S)
        body = msc.group(1) if msc else ""
        # 场景块要有表（| 或 粗体条目），不能只有一句"某地。"
        if not (body.count("|") >= 2 or len(re.findall(r"^\*\*", body, re.M)) >= 1):
            asset_missing = True

    problems = []
    if missing:
        problems.append(f"缺块：{'、'.join(missing)}")
    if not head:
        problems.append("找不到分镜表表头")
    elif bad_cols:
        problems.append(f"列头缺：{'、'.join(bad_cols)}")
    if not quota:
        problems.append("缺验收额度（每镜最多重抽几次 / 失败是停还是继续）")
    if aigc_missing:
        problems.append(f"缺 AIGC 标识（{comp.get('aigc_label_text')}）")
    if asset_missing:
        problems.append("场景未按台账登记（有『格』的形态必须有场景表，防跨镜漂移）")

    # 附带提醒：交付只该有一个 .md，不该夹带提示词或参考图说明
    extras = []
    if re.search(r"提示词|prompt|风格后缀", text, re.I):
        extras.append("正文里出现提示词或风格后缀字样——交付只放定稿")

    return {"ok": not problems, "problems": problems, "warnings": extras,
            "blocks": len(BLOCKS) - len(missing), "columns": len(cols) - len(bad_cols),
            "columns_total": len(cols), "form": frm, "cols": cols}


def render_check(res: dict, name: str = "") -> str:
    out = [C.bold(f"\n  交付格式 · {name or '(未命名)'}"), "  " + "─" * 56]
    FORM_NAME = {"live_action": "真人/AI 写实", "anime": "动漫短剧",
                 "motion_comic": "动态漫", "static_comic": "静态漫"}
    out.append(C.dim(f"  形态  {FORM_NAME.get(res.get('form'), res.get('form', ''))}"
                     f"   列头  {'/'.join(res.get('cols', []))}"))
    out.append(f"  五块结构  {res['blocks']}/5    列头  {res['columns']}/{res.get('columns_total', 6)}")
    if res["ok"]:
        out.append(C.ok("  ✓ 格式完整，可以发"))
    for p in res["problems"]:
        out.append(C.err(f"  ✗ {p}"))
    for w in res["warnings"]:
        out.append(C.warn(f"  ! {w}"))
    return "\n".join(out)


# ------------------------------------------------------------
# 项目脚手架
# ------------------------------------------------------------

def project_readme(name: str, genre: str, fmt: str, const: dict | None = None,
                   form: str | None = None) -> str:
    const = const or CONST
    d = const["drama"]
    frm = normalize_form(form or d.get("form"))
    fname = {"live_action": "真人 / AI 写实", "anime": "动漫短剧",
             "motion_comic": "漫剧（动态漫）", "static_comic": "静态漫 / 条漫"}.get(frm, frm)
    if fmt == "series":
        n = d["series"]["episodes_default"]
        body = f"""# {name}

- 形态：{fname} · 系列连载 · {n} 集 × {d['series']['episode_target_s']}s
- 题材：{genre}

## 一句话

（谁，在什么处境下，因为什么，非做什么不可）

## 分集表

见 `分集表.md`。每集单元线本集结清，主线只推进一步。

## 跨集台账

见 `continuity.md`。**写到第 20 集之前，靠台账记住第 3 集登记了什么**——
人记不住，机器能对账。

## 验收额度

每镜最多重抽 {d['delivery']['retake_max']} 次；失败即{d['delivery']['on_fail']}，不硬凑。
"""
    else:
        body = f"""# {name}

- 形态：{fname} · 单集完结 · 目标 {d['single']['target_duration_s']}s
- 题材：{genre}

## 一句话

（谁，在什么处境下，因为什么，非做什么不可）

## 节拍

见 `节拍表.md`。四段：处境 / 压力 / 反转 / 余味。

## 验收额度

每镜最多重抽 {d['delivery']['retake_max']} 次；失败即{d['delivery']['on_fail']}，不硬凑。
"""
    return body
