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
                    is_object_name, is_row, norm_head, normalize_form, platform_for,
                    sizes_for, split_row)
from plan import ACTS, beats

# 交付定稿的五块
BLOCKS = ["基本信息", "人物表", "场景", "分镜表", "验收额度"]
# ★ 同一块可以有多个名字——**块名也是写法**。
#   老板的提交模板写的是「## 2. 人物」「## 5. 验收与额度」，
#   只认「人物表」「验收额度」会把他照模板填的稿子判成"缺两块"，
#   而这两块明明都在。判据要认实际写法，不是认我当初起的名。
#   注意：别名只加"真同义"，不做到模糊匹配——五块结构是硬约束，
#   放宽成"沾边就算"就等于取消这道闸门。
BLOCK_ALIASES = {
    "人物表": ("人物表", "人物"),
    "验收额度": ("验收额度", "验收与额度", "验收"),
}
COLUMNS = ["镜", "时长", "景别", "谁在画面", "动作一句话", "台词原文"]
# 漫画/动漫向（有"格"的形态）的交付列头：把"动作一句话"换成"画面一句话"，
# "台词原文"换成"气泡对白"——不是改名游戏，是两类交付物确实不同：
# 视频交付要的是"能演的动作"，漫画交付要的是"能画的画面 + 装得下的气泡"。
COMIC_COLUMNS = ["镜", "时长", "景别", "谁在画面", "画面一句话", "气泡对白"]

# 列名别名：老板提交模板写的是「镜号｜目标时长｜…」，语义与「镜｜时长」同一列。
# ★ 别名只收**真同义**的说法，不做模糊匹配——列头校验的价值就在于"写错要报"，
#   放宽成"看起来像就算对"，这道闸门就废了。
_COL_ALIAS = {"镜号": "镜", "目标时长": "时长"}

# 工作表的列头（骨架表）。**和交付列头是两套东西，别混**：
# 交付表是给人看和拍的，工作表现在还要能喂给 scan（运镜/声音）与
# 填表人（开场站位）。多出来的列不是冗余，是"填之前必须先决定的东西"。
WORK_COLUMNS = ["镜", "时长", "景别", "运镜", "开场站位", "画面", "台词", "声音"]
WORK_COLUMNS_PANEL = ["镜", "时长", "景别", "运镜", "开场站位", "画面一句话", "气泡对白"]

# 骨架里的占位提示。空表最容易出的事是"填的人不知道这列要什么"，
# 于是把"开场站位"写成"两人在客厅"——那等于没定。
STAGING_HINT = "（待填：谁在左/右·朝向）"
VISUAL_HINT = "（待填：谁｜动作）"

# 有"格"的形态（交付用漫画列头）。**只有两种漫**——动漫短剧是连续动画，
# 交付仍是视频列头（要的是"能演的动作"与"台词原文"，不是"气泡对白"）。
PANEL_FORMS = ("motion_comic", "static_comic")

SIZES = ("大特写", "特写", "近景", "中景", "中全景", "全景", "远景")

# 「谁在画面」列里不是人名的写法。对账人物表时先剔掉这些，
# 否则"空镜""两人"会被当成两个没登记的角色，误报会立刻把这条判据废掉。
# ★ 与 quality.WHO_NOT_A_NAME 同族纪律：宁可漏，不可吵。
# ★ 第二类同样重要：**这一列可以不是人**——手机屏幕、便签、材料、账单
#   这些"物件特写"在分镜里是合法主体。把它们当人名对账，等于要求
#   人物表登记一部手机。
WHO_STOP = {
    "空镜", "空镜头", "无人", "无人景", "画外", "旁人", "路人", "众人",
    "两人", "三人", "四人", "五人", "多人", "两人同框", "三人同框",
    "二人", "大家", "所有人", "群像", "男", "女", "男声", "女声",
    "画外音", "字幕", "待填", "画面", "同框", "背影", "手部", "局部",
    # —— 物件 / 屏幕 / 介质：这些是画面主体，不是人 ——
    "手机屏幕", "手机", "屏幕", "电脑屏幕", "电脑", "便签", "材料",
    "黑板", "白板", "黑板字", "那碗饭", "碗", "锅", "锅铲", "筷子",
    "空椅子", "名册", "社团名册", "第四把椅子", "欠条", "账单", "诊断书",
    "账单", "照片", "纸条", "信件", "合同", "报表", "门", "窗", "墙",
    "桌子", "椅子", "电梯", "楼道", "走廊", "监控", "画面文字",
    # ★ 这张表是**知识缺口**，不是规则。见到新的物件主体就往里加，
    #   别指望判据自己认出来——它只会说"这人没登记"，然后把整条判据吵到被关掉。
    "八仙桌", "桌面", "床头柜", "收银台", "柜台", "台面",
    "判决书", "协议书", "通报文件", "通报", "文件", "单据", "发票",
    "账本", "病历", "房产证", "菜单", "作业本", "点单本", "同意书",
}


def delivery_columns(const: dict | None = None, form: str | None = None) -> list:
    """本次交付该用哪套列头：有"格"的形态用漫画列头，其余用视频列头。"""
    d = (const or CONST)["drama"]
    frm = normalize_form(form or d.get("form") or "live_action")
    if frm in PANEL_FORMS:
        return list(d.get("delivery", {}).get("comic_columns") or COMIC_COLUMNS)
    return list(d.get("delivery", {}).get("shot_columns") or COLUMNS)


def work_columns(const: dict | None = None, form: str | None = None) -> list:
    """骨架表该用哪套列头。比交付多「运镜」「开场站位」，少「谁在画面」——
    人物并进"画面"写成"谁｜动作"，与交付表的合并方式一致。"""
    d = (const or CONST)["drama"]
    frm = normalize_form(form or d.get("form") or "live_action")
    w = d.get("work") or {}
    if frm in PANEL_FORMS:
        return list(w.get("panel_columns") or WORK_COLUMNS_PANEL)
    return list(w.get("shot_columns") or WORK_COLUMNS)



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
                "size": default_size, "move": "固定", "staging": "",
                "visual": "", "line": "", "sound": "",
            })
    return rows


# 列名 → 行字段。渲染与解析共用同一张表，改名时只改一处。
_COL_FIELD = {"镜": "no", "时长": "sec", "景别": "size", "运镜": "move",
              "开场站位": "staging", "画面": "visual", "画面一句话": "visual",
              "台词": "line", "气泡对白": "line", "声音": "sound"}


def render_md(rows: list[dict], title: str = "", const: dict | None = None,
              form: str | None = None) -> str:
    """渲染骨架表。**按列头驱动，不按固定 6 格拼**——
    早先这里是写死的六个 `{}`，加一列就整表错位。"""
    cols = work_columns(const, form or ((const or CONST)["drama"].get("form")))
    out = []
    if title:
        out.append(f"## 分镜表 · {title}")
        out.append("")
    out.append("| " + " | ".join(cols) + " |")
    out.append("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        cells = []
        for c in cols:
            k = _COL_FIELD.get(c)
            if c == "镜":
                cells.append(str(r["no"]))
            elif c == "时长":
                cells.append(f"{r['sec']}s")
            elif c == "开场站位":
                cells.append(r.get("staging") or STAGING_HINT)
            elif c in ("画面", "画面一句话"):
                cells.append(r.get("visual") or VISUAL_HINT)
            elif c in ("台词", "气泡对白"):
                cells.append(r.get("line") or "—")
            elif c == "声音":
                cells.append(r.get("sound") or "无")
            elif k:
                cells.append(r.get(k) or "（待填）")
            else:
                cells.append("")
        out.append("| " + " | ".join(cells) + " |")
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

def _is_oner(text: str, rows: list) -> bool:
    """一镜到底（oner）：整部只有一个镜头，全程不切。

    ★ 必须**同时**满足两个条件才算：
      1. 基本信息里显式写「一镜到底：是」
      2. 分镜表确实只有 1 行

    只认声明不认行数，等于给了一个**万能开关**——写一句话就能绕过单镜时长、
    景别白名单、场景覆盖三条判据，而且没人会再回头看它。
    反过来只认行数不认声明也不行：9 镜的稿子少写了几行也会变成"1 行"，
    那是漏填，不是一镜到底。**声明与表必须互相印证。**
    """
    if len(rows) != 1:
        return False
    return bool(re.search(r"一镜到底\s*[:：]\s*是", text))


def _check_body(text: str, header: list[str], const: dict | None = None,
                form: str | None = None, notes: list[str] | None = None) -> list[str]:
    """核对分镜表**表体**（表头下面的数据行）。

    查四样，都是机械可判的：
      1. **有没有数据行**——只有表头没有镜头，是空壳。
      2. **镜号是否连续**——1,2,4 会让"第 3 镜"在拍摄现场凭空消失。
      3. **时长是否合法**——"abc" "0s" 这种值说明表没填完或填错了。
      4. **景别是否在册**——写错景别，模型抽出来的提示词就是错的。

    ★ 为什么必须查：交付校验是**发出去之前的最后一道**。
      初版只查表头不看行，实测"镜 1 | 999s | 荒谬景别 | 鬼"照样判
      "格式完整，可以发"——表头对就等于稿子对，这是最坏的假绿。
    """
    problems = []
    if not header:
        return problems

    # 定位表头行，取它下面的表格行
    lines = text.splitlines()
    hidx = None
    for i, ln in enumerate(lines):
        if not (ln.strip().startswith("|") or "｜" in ln):
            continue
        cells = [norm_head(c) for c in split_row(ln)]
        if cells and cells[0] in ("镜", "镜号") and any(c in ("时长", "目标时长") for c in cells):
            hidx = i
            break
    if hidx is None:
        return problems

    rows = []
    for ln in lines[hidx + 1:]:
        s = ln.strip()
        if not (s.startswith("|") or "｜" in s):
            if rows:
                break           # 表体结束
            continue
        cells = split_row(s)
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue             # 分隔行
        rows.append(cells)

    if not rows:
        problems.append("分镜表没有数据行（只有表头，不是定稿）")
        return problems

    notes = notes if notes is not None else []
    oner = _is_oner(text, rows)
    # 声明了一镜到底，表里却有多个镜头——**声明与表不符**。
    # 这种稿子最危险：写的人以为自己拍的是长镜头，实际交付的是分镜表，
    # 现场按哪个来都对不上。
    if not oner and len(rows) > 1 and re.search(r"一镜到底\s*[:：]\s*是", text):
        problems.append(f"声明「一镜到底：是」，但分镜表有 {len(rows)} 行")

    # 镜号连续
    nos = []
    for cells in rows:
        if cells and cells[0].isdigit():
            nos.append(int(cells[0]))
    if not nos:
        problems.append("分镜表没有可识别的镜号")
        return problems
    if nos != list(range(nos[0], nos[0] + len(nos))):
        missing = [n for n in range(nos[0], nos[0] + len(nos)) if n not in set(nos)]
        problems.append(f"镜号不连续（缺 {'、'.join(str(x) for x in missing[:8])}）")

    # 时长合法：**下界与上界都要查**。
    # ★ 只查"是否为正数"是半个判据——"999s" 是正数，一行就能毁掉全片时长。
    #   上界取形态的 sec_max（AI 档 8s / 真人 10s / 漫剧 6s…），
    #   因为超上限的镜头在生成端根本出不来。
    ti = 1 if len(header) > 1 else -1
    if ti >= 0:
        d_ = (const or CONST)["drama"]
        try:
            hard = float((d_.get("shot") or {}).get("sec_max") or 0)
        except (TypeError, ValueError):
            hard = 0.0
        bad, over = [], []
        for cells in rows:
            if len(cells) <= ti:
                continue
            v = cells[ti].strip()
            m = re.search(r"(\d+(?:\.\d+)?)", v)
            if not m or float(m.group(1)) <= 0:
                bad.append(f"镜{cells[0]}({v or '空'})")
            elif hard and float(m.group(1)) > hard:
                over.append(f"镜{cells[0]}({float(m.group(1)):g}s)")
        if bad:
            problems.append(f"时长不是正数：{'、'.join(bad[:6])}")
        if over:
            # ★ 一镜到底的超长镜**不拦，但必须说出来**。
            #   60 秒不切在真人实拍里是合法的（排练、走位、焦点都靠调度），
            #   可它一定超出 AI 生成端的单镜上限——那才是真会失败的地方。
            #   拦下来等于告诉老板"一镜到底不能拍"，那是尺子用反；
            #   静默放过等于让人拿着 60s 去跑生成端，那是另一种坑。
            #   **不假装没事，也不假装不行**：降为提示，写清只走真人实拍。
            if oner:
                notes.append(f"一镜到底：{over[0]}，超过生成端单镜上限 {hard:g}s"
                             f"——这一镜只能真人实拍，AI 生成档做不了")
            else:
                problems.append(f"时长超过单镜上限 {hard:g}s：{'、'.join(over[:6])}"
                                f"（超了生成端出不来）")

    # 景别在册：写错景别，模型抽出来的提示词就是错的
    if "景别" in header:
        si = header.index("景别")
        allowed = sizes_for(const, form)
        bad_sz = []
        for cells in rows:
            if len(cells) <= si:
                continue
            v = cells[si].strip()
            if v and v not in allowed:
                # ★ 一镜到底的景别**必然在镜内变化**（"全景起，镜内推至近景"）。
                #   要求它写成一个词，是拿分镜的规矩管长镜头——长镜头没有"一个景别"。
                #   取首个命中的白名单词判起点景别；连起点都不在册才报。
                if oner and any(v.startswith(a) for a in allowed):
                    continue
                bad_sz.append(f"镜{cells[0]}({v})")
        if bad_sz:
            problems.append(f"景别不在白名单：{'、'.join(bad_sz[:6])}（可用：{'、'.join(allowed)}）")

    # ★ 基本信息里的「约 N 秒 / M 镜」必须和分镜表加起来一致。
    #   这条最容易漏：改分镜时加了一镜、砍了两秒，抬头那句"约60秒9镜"没人回头改，
    #   于是稿子上写着 60 秒、实际 56 秒，一路送到生成端才被时长坑到。
    #   老板的模板把「内部不能矛盾」写在人物那一节，但**数字的自洽同样是自洽**。
    #   容差给 3%：写的是"约"，但 60 秒的片子差 4 秒就是 7%，不能算"约"。
    #   镜数必须精确——少一镜现场就少一镜，没有"约 9 镜"这回事。
    info_m = re.search(r"^#+\s*(?:[一二三四五六七八九十\d]+\s*[、.．]?\s*)?基本信息[^\n]*$"
                       r"(.*?)(?=^#|\Z)", text, re.M | re.S)
    if info_m and rows:
        blk = info_m.group(1)
        msec = re.search(r"约\s*(\d+(?:\.\d+)?)\s*秒", blk)
        mn = re.search(r"(\d+)\s*镜", blk)
        ti2 = 1 if len(header) > 1 else -1
        real_sec = 0.0
        if ti2 >= 0:
            for cells in rows:
                if len(cells) > ti2:
                    mm = re.search(r"(\d+(?:\.\d+)?)", cells[ti2])
                    if mm:
                        real_sec += float(mm.group(1))
        if msec and real_sec:
            said = float(msec.group(1))
            if abs(said - real_sec) > max(1.0, said * 0.03):
                problems.append(f"基本信息写「约{said:g}秒」，分镜表实际加起来 {real_sec:g} 秒")
        if mn and int(mn.group(1)) != len(rows):
            problems.append(f"基本信息写「{mn.group(1)} 镜」，分镜表实际 {len(rows)} 行")

    # 画面/台词列不能整列是占位符（没填的骨架不能当定稿发出去）
    for label in ("动作一句话", "画面一句话"):
        if label not in header:
            continue
        ci = header.index(label)
        empty = [c[0] for c in rows
                 if len(c) > ci and c[ci].strip() in ("（待填）", "（待填：谁｜动作）", "")]
        if empty and len(empty) == len(rows):
            problems.append(f"「{label}」整列没填（还是骨架，不是定稿）")
        elif empty:
            problems.append(f"「{label}」有 {len(empty)} 镜没填（{'、'.join(empty[:6])}）")
    return problems


def check_delivery(text: str, const: dict | None = None,
                   form: str | None = None, is_ai: bool = True) -> dict:
    """检查定稿是否满足交付格式。

    查六样：五块标题齐不齐、分镜表六列表头对不对、**分镜表本身有没有内容**、
    镜号连不连续、验收额度有没有落数、AIGC 标识在不在（AI 生成内容必查）。
    缺一样就别发。

    列头分两套：视频交付（动作一句话 / 台词原文）与漫画交付（画面一句话 /
    气泡对白）。**按形态取，不是二选一**——写错列头等于交错了东西。

    ★ 表体必须查。初版只查表头不看行，于是"999 秒 / 荒谬景别 / 鬼"这种
      行照样判"格式完整，可以发"——**表头对就等于稿子对**，这是最坏的假绿：
      交付校验是发出去之前的最后一道，它说"可以发"就是真的一锤定音。
      所以这里至少要核：有没有数据行、镜号是否连续、时长是否合法、景别是否在册。
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
        for alias in BLOCK_ALIASES.get(name, (name,)):
            pat = rf"^#+\s*(?:[一二三四五六七八九十\d]+\s*[、.．]?\s*)?{re.escape(alias)}"
            if re.search(pat, text, re.M):
                return True
        return False

    missing = [b for b in BLOCKS if not _has_block(b)]

    head = ""
    for line in text.splitlines():
        s = line.strip()
        if not (s.startswith("|") or "｜" in s):
            continue
        cells = [norm_head(c) for c in split_row(s)]
        if cells and cells[0] in ("镜", "镜号") and any(c in ("时长", "目标时长") for c in cells):
            head = s
            break
    got = [_COL_ALIAS.get(norm_head(c), norm_head(c)) for c in split_row(head)] if head else []
    bad_cols = [c for c in cols if c not in got]

    # ★ 表体核对：数据行、镜号连续、时长合法
    body_notes: list[str] = []
    body_problems = _check_body(text, got, {"drama": d}, frm, body_notes)

    # 验收额度必须落在"每镜几次 + 失败怎么办"这两件事上。
    # 用词分两种：AI 生成叫"重抽"，真人实拍叫"拍几条"，两种都认。
    quota_text = ""
    mq = re.search(r"^#+\s*(?:[一二三四五六七八九十\d]+\s*[、.．]?\s*)?验收(?:与)?额度[^\n]*$(.*?)(?=^#|\Z)",
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
        msc = re.search(r"^#+\s*(?:[一二三四五六七八九十\d]+\s*[、.．]?\s*)?场景[^\n]*$(.*?)(?=^#|\Z)",
                        text, re.M | re.S)
        body = msc.group(1) if msc else ""
        # 场景块要有表（| 或 粗体条目），不能只有一句"某地。"
        if not (body.count("|") >= 2 or len(re.findall(r"^\*\*", body, re.M)) >= 1):
            asset_missing = True

    # ★ 跨镜可追溯（consistency_check）：分镜表里出场的人，必须在人物表登记过。
    #   语义是"这四类跨镜必须可追溯"，其中**角色**是唯一能从交付稿机械对账的一类
    #   （服装/道具/表情没有对应列，见下方 asset_unverifiable）。
    #   ⚠️ 这条能查是靠"人物表 + 谁在画面"两处文本交集，不做语义理解；
    #   所以只报"出场了但没登记"，不报"登记了但没出场"（后者可能是有意留白）。
    unregistered = []
    cc = asset_block.get("consistency_check") or []
    if "角色" in cc and got and "谁在画面" in got:
        reg_block = re.search(
            r"^#+\s*(?:[一二三四五六七八九十\d]+\s*[、.．]?\s*)?(?:人物表|人物)[^\n]*$(.*?)(?=^#|\Z)",
            text, re.M | re.S)
        reg = reg_block.group(1) if reg_block else ""
        used = set()
        for line in text.splitlines():
            if not is_row(line):
                continue
            cells = split_row(line)
            # ★ 只认"以数字开头"的数据行。表头行本身也是一行表格，
            #   不排掉它，"谁在画面"这四个字会被当成一个演员名报出来
            #   ——「分镜表出场但人物表未见登记：谁在画面」这种鬼话就是这么来的。
            if len(cells) != len(got) or not cells[0].isdigit():
                continue
            who = cells[got.index("谁在画面")]
            # ★ `·` 与 `・` 是老板模板里的人名分隔符（"周海 · 小李"）。
            #   不放进分隔符里，整串会被当成**一个人名**去比对，然后报"未登记"。
            for name in re.split(r"[、,，/／+＋和与·・\s]+", who):
                # ★ 顺序不能反：**先剥括注，再 strip 首尾括号**。
                #   反了就出事——「13楼女人（末6秒入画）」先 strip 掉右括号，
                #   变成不闭合的「13楼女人（末6秒入画」，正则匹配不上，
                #   整个人名连注释一起被报"未登记"。判据自己读瞎，比漏判更坏：
                #   它逼人去改本来没写错的稿子。
                name = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
                name = name.strip("（）()【】[]·・-")
                if (len(name) >= 2 and name not in WHO_STOP
                        and not is_object_name(name)
                        and not re.fullmatch(r"[\d.]+s?", name)):
                    used.add(name)
        for name in sorted(used):
            if name not in reg:
                unregistered.append(name)

    problems = []
    if missing:
        problems.append(f"缺块：{'、'.join(missing)}")
    if not head:
        problems.append("找不到分镜表表头")
    elif bad_cols:
        problems.append(f"列头缺：{'、'.join(bad_cols)}")
    problems += body_problems
    if not quota:
        problems.append("缺验收额度（每镜最多重抽几次 / 失败是停还是继续）")
    if aigc_missing:
        problems.append(f"缺 AIGC 标识（{comp.get('aigc_label_text')}）")
    if asset_missing:
        problems.append("场景未按台账登记（有『格』的形态必须有场景表，防跨镜漂移）")

    # 附带提醒：交付只该有一个 .md，不该夹带提示词或参考图说明
    extras = []
    extras.extend(body_notes)
    # ★ 人物对账降为**提醒**，不是阻断：这条靠"谁在画面"列的文本切分猜人名，
    #   列里合法地会出现物件（手机屏幕/便签）、群体（两人）、无主体（空镜）。
    #   判错了就拦稿，人立刻会把这条关掉——**宁可漏，不可吵**。
    if unregistered:
        extras.append("分镜表出场但人物表未见登记："
                      + "、".join(unregistered)
                      + "（若为道具/群体可忽略；若确是人，请补进人物表）")
    if re.search(r"提示词|prompt|风格后缀", text, re.I):
        extras.append("正文里出现提示词或风格后缀字样——交付只放定稿")
    # 有声明却无数据源的资产项：明确说出来，不静默当通过
    nv = [k for k in ("expression_enum", "costume_enum_required", "char_views")
          if asset_block.get(k)]
    if nv:
        extras.append("资产项 " + "、".join(nv)
                      + " 在交付稿里没有对应列，本工具查不到——需人工核对")

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
