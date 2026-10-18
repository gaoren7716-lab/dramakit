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

from common import CONST, C
from plan import ACTS, beats

# 交付定稿的五块
BLOCKS = ["基本信息", "人物表", "场景", "分镜表", "验收额度"]
COLUMNS = ["镜", "时长", "景别", "谁在画面", "动作一句话", "台词原文"]

SIZES = ("大特写", "特写", "近景", "中景", "中全景", "全景", "远景")


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

    rows, no = [], 0
    for b, n in zip(rows_beats, per_act):
        step = round(b["sec"] / n, 1) if n else b["sec"]
        for k in range(n):
            no += 1
            rows.append({
                "no": no, "act": b["name"], "sec": step,
                "size": "中景", "who": "", "action": "", "line": "",
            })
    return rows


def render_md(rows: list[dict], title: str = "") -> str:
    out = []
    if title:
        out.append(f"## 分镜表 · {title}")
        out.append("")
    out.append("| " + " | ".join(COLUMNS) + " |")
    out.append("|" + "|".join(["---"] * len(COLUMNS)) + "|")
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

def check_delivery(text: str, const: dict | None = None) -> dict:
    """检查定稿是否满足交付格式。

    查三样：五块标题齐不齐、分镜表六列表头对不对、验收额度有没有落数。
    这三样是"发出去之前"的最后一道，缺一样就别发。
    """
    const = const or CONST
    d = const["drama"]["delivery"]
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
    cols = [c.strip() for c in head.strip("|").split("|")] if head else []
    bad_cols = [c for c in COLUMNS if c not in cols]

    # 验收额度必须落在"每镜几次 + 失败怎么办"这两件事上。
    # 用词分两种：AI 生成叫"重抽"，真人实拍叫"拍几条"，两种都认。
    quota_text = ""
    mq = re.search(r"^#+\s*(?:[一二三四五六七八九十]+\s*[、.．]\s*)?验收额度\s*$(.*?)(?=^#|\Z)",
                   text, re.M | re.S)
    if mq:
        quota_text = mq.group(1)
    quota = bool(re.search(r"\d", quota_text)) and bool(
        re.search(r"重抽|重拍|重试|拍\s*\d|条|次|不过|失败|停", quota_text))

    problems = []
    if missing:
        problems.append(f"缺块：{'、'.join(missing)}")
    if not head:
        problems.append("找不到分镜表表头")
    elif bad_cols:
        problems.append(f"列头缺：{'、'.join(bad_cols)}")
    if not quota:
        problems.append("缺验收额度（每镜最多重抽几次 / 失败是停还是继续）")

    # 附带提醒：交付只该有一个 .md，不该夹带提示词或参考图说明
    extras = []
    if re.search(r"提示词|prompt|参考图|风格后缀", text, re.I):
        extras.append("正文里出现提示词或参考图字样——交付只放定稿")

    return {"ok": not problems, "problems": problems, "warnings": extras,
            "blocks": len(BLOCKS) - len(missing), "columns": len(COLUMNS) - len(bad_cols)}


def render_check(res: dict, name: str = "") -> str:
    out = [C.bold(f"\n  交付格式 · {name or '(未命名)'}"), "  " + "─" * 56]
    out.append(f"  五块结构  {res['blocks']}/5    六列表头  {res['columns']}/6")
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

def project_readme(name: str, genre: str, fmt: str, const: dict | None = None) -> str:
    const = const or CONST
    d = const["drama"]
    if fmt == "series":
        n = d["series"]["episodes_default"]
        body = f"""# {name}

- 形态：系列连载 · {n} 集 × {d['series']['episode_target_s']}s
- 题材：{genre}

## 一句话

（谁，在什么处境下，因为什么，非做什么不可）

## 分集表

见 `分集表.md`。每集单元线本集结清，主线只推进一步。

## 验收额度

每镜最多重抽 {d['delivery']['retake_max']} 次；失败即{d['delivery']['on_fail']}，不硬凑。
"""
    else:
        body = f"""# {name}

- 形态：单集完结 · 目标 {d['single']['target_duration_s']}s
- 题材：{genre}

## 一句话

（谁，在什么处境下，因为什么，非做什么不可）

## 节拍

见 `节拍表.md`。四段：处境 / 压力 / 反转 / 余味。

## 验收额度

每镜最多重抽 {d['delivery']['retake_max']} 次；失败即{d['delivery']['on_fail']}，不硬凑。
"""
    return body
