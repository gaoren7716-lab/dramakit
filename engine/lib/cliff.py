# -*- coding: utf-8 -*-
"""卡点评分（cliff）：结尾镜值不值得让人掏钱。

**为什么单列一个模块**：连载的生死线不在单集好不好看，在**卡点**。
观众看到第 9 集结尾那一秒，决定要不要付第 10 集的钱。这个"那一秒"
是机械可判的——它是分镜表里最后一个镜，而那个镜的全部信息都在表里。

之前 [cliff] 与 [cliff.score] 两张表写得很细，但**一行都没被执行过**：
整个项目 grep 不到 "cliff"。表写得像回事、实际是空转，比没有这张表更坏——
它让人以为"付费卡点有判据管着"。

评分口径（百分制）：
  - 加分项命中权重之和 / 加分项满分权重之和 → 折算成百分制基准分；
  - 减分项从基准分里直接扣；
  - 下限 0，上限 100。

★ 为什么折算而不是直接用原始加权和：原始满分只有 56 分，
  写 "cliff_score_min = 85" 意味着**永远达不到**。这类"永远红"的判据
  比没有判据更糟——它给人虚假的安全感。
  折算后：满分 100，阈值 70，命中 4/6 项就能过。

三条硬规则：
  1. 结尾镜必须有钩子（否则没有卡点可言）；
  2. 卡点评分不低于 cliff_score_min；
  3. 付费集前一集是情绪低谷（情绪净值由分集表给，本模块只核对一致性）。

纯标准库，零依赖。
"""
from __future__ import annotations

import re

from common import C, is_row, split_row

# 结尾镜的"钩子词"：台词里有这些，说明这一句不是陈述，是悬着的一口气。
# ★ 词表要收**实际会写出来的说法**，不是理论分类。
#   只收"凭什么/为什么"这类书面词，实测"你想干什么？""这事跟你有什么关系？"
#   这种最常见的口语钩子全判不中，30 分直接不合格——判据与写法脱节。
_HOOK_LINE = (
    "凭什么", "为什么", "为啥", "难道", "竟然", "居然", "没想到", "其实",
    "真相", "秘密", "骗", "撒谎", "闭嘴", "你敢", "干什么", "做什么",
    "关你", "跟你有", "什么意思", "怎么回事", "凭什么你", "你到底",
    "别装", "少来", "不可能", "不可能是", "你是谁", "我知道了", "等等",
)

# 结尾镜的"未答问题"：疑问句收尾
_QUESTION = re.compile(r"[？?]\s*$")

# 结尾镜的"陈述句收尾"：太平了，没东西发生
_FLAT_END = re.compile(r"[。．.!！]\s*$")

# 情绪特写（按形态不同，见 config [sizes] 与 [panel].sizes）
_EMOTION_SIZES = ("特写", "大特写", "特写格")

# 信息反转信号词（从 config [cliff.words].info_reverse 取，这里是兜底）
_DEFAULT_INFO_REVERSE = (
    "原来是你", "原来是他", "原来是", "竟然是你", "竟然是他", "竟然是",
    "真相是", "真相就是", "就是她", "就是他", "我是她", "我是他",
    "其实我", "其实他", "其实她", "一直以为", "从一开始", "一直以来",
    "你才是", "我才是", "我们都是", "你们都是", "不是别人",
    "转过身", "摘下", "撕开", "露出来", "露出", "摊开", "揭开",
)

# 靠旁白/字幕交代（而不是演出来）
_NARRATION_MARKS = ("旁白", "字幕", "屏幕字", "标题卡")


def _cliff_cfg(d: dict) -> dict:
    return (d or {}).get("cliff") or {}


def score_ending(shots: list[dict], d: dict, close_sizes=None) -> dict:
    """给结尾镜打分。返回 {score, band, hits, detail, ok}。

    `d` 是合过形态/档位/项目覆盖的 drama 表。
    """
    cf = _cliff_cfg(d)
    sc_cfg = cf.get("score") or {}
    words_cfg = (cf.get("words") or {})
    info_words = tuple(words_cfg.get("info_reverse") or _DEFAULT_INFO_REVERSE)

    if not shots:
        return {"score": 0, "band": "无镜", "hits": [], "detail": "没有镜头",
                "ok": False, "min": int(cf.get("cliff_score_min") or 70)}

    last = shots[-1]
    line = (last.get("line") or "").strip()
    visual = (last.get("visual") or "").strip()

    # 加分项：逐项判定，命中即记
    bonus_items = {
        "line_hook": any(w in line for w in _HOOK_LINE),
        "visual_event": bool(re.search(r"震动|来电|消息|响起|铃声|突然|瞬间|推门|门被推开|起身|站起|跪|耳光|摔|撕|转身走|拉住|抱住|亮起|闪|空无一人|空了|没人|湿透|攥紧|停在|没动|回头|低头看", visual)),
        "emotion_close": last.get("size") in (_EMOTION_SIZES if close_sizes is None else close_sizes),
        "info_reverse": bool(any(w in line or w in visual for w in info_words)),
        "cliff_question": bool(_QUESTION.search(line)),
        "call_back": False,      # 见下方：需要全片上下文
    }

    # call_back：结尾镜的台词/画面里，有 token 在前面某一镜出现过。
    # 这是"回收了早期伏笔"的最硬证据——同一个 token 隔了几十秒再出现。
    call_back_detail = ""
    if len(shots) > 1:
        earlier = " ".join((s.get("line") or "") + " " + (s.get("visual") or "")
                           for s in shots[:-1])
        # 取结尾镜里的 2-4 字中文片段，只认长度 ≥2 的实词
        cands = set()
        for src in (line, visual):
            for seg in re.split(r"[，。！？；：、…—·|｜\s（）()\"']+", src):
                for size in (2, 3, 4):
                    for k in range(max(0, len(seg) - size + 1)):
                        cands.add(seg[k:k + size])
        hit = [c for c in sorted(cands, key=len, reverse=True)
               if len(c) >= 2 and c in earlier
               and c not in ("我们", "什么", "这个", "那个", "自己", "一下", "没有",
                             "一个", "时候", "知道", "怎么", "已经", "还是", "可以")]
        if hit:
            bonus_items["call_back"] = True
            call_back_detail = f"（回收「{hit[0]}」）"

    # 减分项
    penalty_items = {
        "flat_ending": bool(line and _FLAT_END.search(line)),
        "narration_tell": any(m in line for m in _NARRATION_MARKS),
    }

    # 计分：基础项满分 100 + 高级项加分（上限锁 100）− 减分项
    # ★ 口径必须写在配置里、由代码读，不能各算各的：
    #   早先"门槛按原始加权和写、判据按百分制读"，两边差着一倍，
    #   于是这张表永远红——而没人发现，因为它压根没被执行过。
    base_keys = ("line_hook", "visual_event", "emotion_close", "cliff_question")
    bonus_keys = ("info_reverse", "call_back")
    penalty_keys = ("flat_ending", "narration_tell")
    base_max = sum(float(sc_cfg.get(k, 0)) for k in base_keys)
    base_got = sum(float(sc_cfg.get(k, 0)) for k in base_keys
                   if bonus_items.get(k))
    bonus_got = sum(float(sc_cfg.get(k, 0)) for k in bonus_keys
                    if bonus_items.get(k))
    deduct = sum(float(sc_cfg.get(k, 0)) for k in penalty_keys
                 if penalty_items.get(k))
    score = max(0.0, min(100.0, base_got + bonus_got - deduct))

    minimum = int(cf.get("cliff_score_min") or 70)
    hits = [k for k in base_keys + bonus_keys if bonus_items.get(k)]
    pens = [k for k in penalty_keys if penalty_items.get(k)]
    band = ("强" if score >= minimum else
            "偏弱" if score >= minimum * 0.7 else "不合格")

    detail = (f"命中 {len(hits)}/{len(base_keys) + len(bonus_keys)} 加分项"
              + (f"（{'、'.join(hits)}{call_back_detail}）" if hits else "")
              + (f"；{'、'.join(pens)} 减分" if pens else "")
              + f"；{score:.0f}/{minimum} 分")

    return {"score": round(score, 1), "band": band, "hits": hits,
            "penalties": pens, "detail": detail,
            "ok": score >= minimum, "min": minimum,
            "line": line, "visual": visual}


def render(res: dict, title: str = "") -> str:
    out = [C.bold(f"\n  卡点评分 · {title or '(未命名)'}"), "  " + "─" * 56]
    sc = res["score"]
    lo = res.get("min", 70)
    color = C.ok if res.get("ok") else C.err
    bar_full = 20
    filled = int(round(sc / 100 * bar_full))
    out.append(f"  {sc:>5.1f} 分   {color(res['band'])}"
               + C.dim(f"   （门槛 {lo} 分）"))
    out.append(f"  {'█' * filled}{'░' * (bar_full - filled)}")
    out.append(C.dim(f"  {res['detail']}"))
    if res.get("line"):
        out.append(C.dim(f"  结尾镜台词：{res['line'][:40]}"))
    out.append("")
    return "\n".join(out)


# ------------------------------------------------------------
# 分集表节奏（series 专用）
# ------------------------------------------------------------
# 单集卡点好看，整季还是可能崩：卡点挤堆（连着三集都高潮，观众麻木）、
# 或者连憋十集一次不放（观众弃剧）。这两件事**在分集表上就是可算的**，
# 不需要读剧本。
#
# ★ 这批判据此前是 4 个空转的 config 键（paywall_valley_required /
#   climax_min_gap / emotion_release_every / valence_max）——表写了、
#   没人读。v0.6.0 落地：valence（情绪净值）由分集表的一列给出，
#   规则全部作用在那一列上。
#
# 分集表列：集 | 时长 | 情绪净值 | 单元线 | 主线推进 | 结尾钩子 | 标记
# `情绪净值` 是作者填的整数（−valence_max … +valence_max）：
#   负数 = 憋屈/压抑，正数 = 爽/释放。工具不算这个值，只查它的分布。

VALENCE_COLS = ("情绪净值", "情绪值", "净值")


def _parse_episode_table(text: str) -> tuple[list[dict], str]:
    """从分集表 Markdown 里取每集的行，返回 (行列表, 问题描述)。"""
    rows = []
    header = []
    for line in text.splitlines():
        if not is_row(line):
            continue
        cells = split_row(line)
        if set("".join(cells)) <= {"-", ":"}:
            continue
        if not header:
            if "集" in cells[0] or "集" in "".join(cells[:2]):
                header = cells
            continue
        if len(cells) < 2:
            continue
        try:
            no = int(re.sub(r"[^\d]", "", cells[0]) or 0)
        except ValueError:
            continue
        if no <= 0:
            continue
        rec = {"no": no, "cells": cells}
        for col in VALENCE_COLS:
            if col in header:
                i = header.index(col)
                if i < len(cells):
                    try:
                        rec["valence"] = int(re.sub(r"[^\-\d]", "", cells[i]) or 0)
                    except ValueError:
                        pass
                break
        # 标记列：认"大高潮/小高潮/付费卡点"
        mark = " ".join(cells)
        rec["mark"] = mark
        rows.append(rec)
    return rows, (",".join(header) if header else "")


def check_series_rhythm(text: str, d: dict) -> tuple[list, list, dict]:
    """分集表的节奏判据。返回 (blocking, warning, info)。

    四条规则，对应 config `[cliff]` 里原本空转的四个键：
      1. `paywall_valley_required` —— 付费集的前一集必须是情绪净值最低谷
      2. `climax_min_gap`          —— 两个高潮之间至少隔 N 集
      3. `emotion_release_every`   —— 每 N 集至少一次情绪释放（净值 > 0）
      4. `valence_max`             —— 情绪净值的合法量程，越界即数据错
    """
    cf = _cliff_cfg(d)
    blocking, warning, info = [], [], {}
    rows, header = _parse_episode_table(text or "")
    if not rows:
        return blocking, warning, info

    info["episodes"] = len(rows)
    info["valence_col"] = any("valence" in r for r in rows)
    vmax = int(cf.get("valence_max") or 5)
    by_no = {r["no"]: r for r in rows}

    # 4) 量程：越界说明填错了，不是节奏问题，阻断
    oob = [r["no"] for r in rows
           if "valence" in r and not (-vmax <= r["valence"] <= vmax)]
    if oob:
        blocking.append({
            "check": "情绪净值越界",
            "value": "第 " + "/".join(str(x) for x in oob) + " 集",
            "limit": f"合法量程 −{vmax} … +{vmax}（config valence_max）",
        })

    # 1) 付费集前一集是最低谷
    if cf.get("paywall_valley_required") and info["valence_col"]:
        pay = None
        for r in rows:
            if "付费" in r.get("mark", ""):
                pay = r["no"]
                break
        if pay and pay in by_no and (pay - 1) in by_no:
            vals = {n: rr["valence"] for n, rr in by_no.items() if "valence" in rr}
            if vals:
                lo = min(vals.values())
                prev = by_no[pay - 1].get("valence")
                if prev is not None and prev != lo:
                    warning.append({
                        "check": "付费卡点前不是情绪低谷",
                        "value": f"第 {pay - 1} 集净值 {prev}，全季最低 {lo}"
                                 f"（第 {[n for n, v in vals.items() if v == lo][0]} 集）",
                        "limit": "付费前一集必须是最低谷——先憋到最狠再给翻盘",
                    })

    # 2) 高潮最小间隔
    gap = int(cf.get("climax_min_gap") or 0)
    if gap > 1:
        peaks = [r["no"] for r in rows if "高潮" in r.get("mark", "")]
        for a, b in zip(peaks, peaks[1:]):
            if b - a < gap:
                warning.append({
                    "check": "高潮挤堆",
                    "value": f"第 {a} 集与第 {b} 集只隔 {b - a} 集",
                    "limit": f"两个高潮至少隔 {gap} 集（挤一起观众会麻木）",
                })
                break

    # 3) 每 N 集至少一次情绪释放
    every = int(cf.get("emotion_release_every") or 0)
    if every > 1 and info["valence_col"]:
        run = 0
        worst = 0
        for r in rows:
            v = r.get("valence")
            if v is None:
                run = 0
                continue
            run = run + 1 if v <= 0 else 0
            worst = max(worst, run)
        if worst >= every:
            warning.append({
                "check": "连续压抑过久",
                "value": f"最长连续 {worst} 集情绪净值为非正",
                "limit": f"每 {every} 集至少一次释放（一直憋观众会弃剧）",
            })

    info["valence_range"] = (
        min(r["valence"] for r in rows if "valence" in r),
        max(r["valence"] for r in rows if "valence" in r),
    ) if info["valence_col"] else None
    return blocking, warning, info


def render_rhythm(blocking: list, warning: list, info: dict) -> str:
    out = [C.bold("\n  分集节奏"), "  " + "─" * 56]
    if not info.get("episodes"):
        out.append(C.dim("  分集表里没解析到集行——跳过"))
        out.append("")
        return "\n".join(out)
    out.append(f"  集数 {info['episodes']}"
               + (f"    情绪净值 {info['valence_range'][0]} … {info['valence_range'][1]}"
                  if info.get("valence_range") else
                  C.dim("    未填「情绪净值」列，节奏判据查不了")))
    for b in blocking:
        out.append(C.err(f"  ✗ {b['check']}：{b['value']}（{b['limit']}）"))
    for w in warning:
        out.append(C.warn(f"  ! {w['check']}：{w['value']}（{w['limit']}）"))
    if not blocking and not warning:
        out.append(C.ok("  ✓ 节奏没问题"))
    out.append("")
    return "\n".join(out)
