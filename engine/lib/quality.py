# -*- coding: utf-8 -*-
"""故事质量评分卡（[story]）：量"有没有戏"的机械代理。

与 scan 的分工：**scan 量镜头语言（可阻断），本模块量结构特征（只参考）。**
scan 回答"镜头表合不合格"，本模块回答"这个本子的结构特征齐不齐"——
事情在发生、三幕都有揭示、空间在换、不靠旁白点题、有人可冲突。

先把一句实话写在前面：**戏好不好，最终不可机械判。** 演技、共情、
真实感、审美，都量不出来。这里量的全是代理指标——爆款短剧反复出现的
结构特征。所以它是参考分，不是门槛，不拦稿。

三条纪律（与 config [story] 注释一致）：
  1. 词表只收多字短语——单字会命中日常词，误报多了人就关掉这项；
  2. 不可验的维度明说"不可验"，不假装量过；
  3. 判据必须能被语料证伪（_test/quality_corpus.json + selftest P 组）。

用法：
    python engine/lib/quality.py <分镜表.md> [--json]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from common import C, CONST, constants_for  # noqa: E402

ST_ = (CONST.get("drama") or {}).get("story") or {}

# 谁在画面列的占位符：解析前先剔掉，否则"（待填）"会被当成一个人物
WHO_PLACEHOLDERS = {"", "无", "待填", "（待填）", "—", "-", "略"}

# ★ 不是人名、但写在"谁在画面"列里的东西。数它们会把人物数虚增，
#   冲突面因此虚高——这正是"按名词查表"翻车的老坑。
#   "空镜"是没有人的镜头；"两人"是数量描述不是名字；"三人同框"同理。
WHO_NOT_A_NAME = {
    "空镜", "空镜头", "无人", "无人景", "画外", "旁人", "路人", "众人",
    "两人", "三人", "四人", "五人", "多人", "两人同框", "三人同框",
    "二人", "大家", "所有人", "众人同框", "群像",
    "男", "女", "男声", "女声", "画外音", "字幕",
    # ★ 物件 / 屏幕 / 介质：分镜里"谁在画面"合法地可以不是人。
    #   不剔的后果和"空镜当人名"一样——一个人物的戏被数成三四个，
    #   冲突面的人物分虚高。实测《不属实》只有老师与主任两人，
    #   却因"手机屏幕""材料"被数成 5 个人物。
    #   ★ 与 shotlist.WHO_STOP 必须同步：两处查的是同一件事。
    "手机屏幕", "手机", "屏幕", "电脑屏幕", "电脑", "便签", "材料",
    "账单", "照片", "纸条", "信件", "合同", "报表", "门", "窗", "墙",
    "桌子", "椅子", "电梯", "监控", "画面文字", "门缝", "镜子",
    "同意书", "签字表", "表格", "纸", "轮椅", "床", "窗帘",
}


def _is_real_name(w: str) -> bool:
    """这个"谁在画面"格子里写的是不是一个人名。"""
    if not w or w in WHO_PLACEHOLDERS or w in WHO_NOT_A_NAME:
        return False
    # ★ 单个字符一律不是人名，标点更不是。
    #   "老师 · 主任"按空白切开后会剩下一个"·"，它曾被当成第三个人物——
    #   人物数从 2 虚增到 3，冲突面直接多算一分。**标点不是人。**
    if len(w) < 2:
        return False
    if not re.search(r"[\u4e00-\u9fa5A-Za-z]", w):
        return False
    # "两人"这类数量词：含"人/众/位/名"且前面是数字 → 数量描述
    if re.fullmatch(r"[0-9一二三四五六七八九十两几多]+[人众位名]", w):
        return False
    return True


# 子句切分：token 只在子句内滑窗产出，跨子句不组词。
# ★ ｜ 必须在册——交付格式的 visual 是"谁｜动作"，不切它就会产出
#   "周宁｜车"这种缝合怪 token，呼应数直接虚胖。
_CLAUSE_SPLIT = re.compile(r"[，。！？；：、…—·|｜\"'\"'()（）\s]+")


def _drama(d: dict | None) -> dict:
    """取合并后的 drama 常数；缺 [story] 表时退回全局默认。"""
    if not d:
        return CONST["drama"]
    if "story" not in d:
        d = dict(d)
        d["story"] = ST_
    return d


def _tbl(st: dict, name: str) -> dict:
    t = st.get(name)
    return t if isinstance(t, dict) else {}


def _words(tbl: dict) -> tuple:
    return tuple(tbl.get("words") or ())


def _hits(text: str, words: tuple) -> set:
    """返回命中的词集合（不是次数——同一词反复出现只算一个反转）。"""
    return {w for w in words if w and w in text}


def _shot_text(s: dict) -> str:
    """该镜的可检索文本：画面 + 台词。对白是揭示的主战场，画面是动作的主战场。"""
    return f"{s.get('visual') or ''} {s.get('line') or ''}"


def _who_names(s: dict) -> set:
    """从 6 列交付表的 visual（"谁｜动作"）解析人物名。

    7 列工作表没有"谁在画面"列，返回空集且不可验——调用方必须注明，
    不许把"画面里没人名"当成"只有一个角色"。

    ★ 必须剔掉占位与数量描述（"空镜""两人""三人同框"）。
      剔不掉的后果：一个人物的镜头会被数成三四个人，冲突面的人物分
      直接虚高——实测《十七楼》明明只有周牧与女人两人，却报"4 个人物"，
      拿满 15/15。这跟"胶水词混进呼应"是同一族病：查到了词，
      但那个词不是这个维度要的东西。
    """
    visual = s.get("visual") or ""
    if "｜" not in visual:
        return set()
    who = visual.split("｜", 1)[0]
    for sep in ("、", "，", ",", "/", "和", "与"):
        who = who.replace(sep, " ")
    out = set()
    for w in who.split():
        # ★ 定语归一：分镜表里合法地会写"床上的父亲""一楼的父亲"
        #   ——这是为了让拍摄时看清"谁在画面的哪个位置"，不是两个人物。
        #   取"的"后面那段当人名，否则同一个人被数成两三个，冲突面虚高。
        if "的" in w:
            w = w.rsplit("的", 1)[1]
        if _is_real_name(w):
            out.add(w)
    return out


# ------------------------------------------------------------
# 五个维度
# ------------------------------------------------------------

def dim_events(shots: list[dict], st: dict) -> dict:
    """事件密度：发生事情的镜头占比。两人坐着说完全场，这里是 0。"""
    ev = _tbl(st, "events")
    words = _words(ev)
    w = int(st.get("w_events") or 25)
    n = len(shots)
    hit = [s for s in shots if _hits(_shot_text(s), words)]
    ratio = len(hit) / n if n else 0.0
    full = float(ev.get("full_ratio") or 0.5)
    sc = min(w, w * ratio / full) if full > 0 else 0.0
    return {"name": "事件密度", "score": round(sc, 1), "max": w,
            "detail": f"{len(hit)}/{n} 镜有外部事件（满配占比 {full:.0%}）",
            "hits": sorted(_hits(" ".join(_shot_text(s) for s in hit), words))[:6]}


def dim_echoes(shots: list[dict], st: dict) -> dict:
    """呼应结构：埋下去的钉子，后面有人捡。

    不靠词表，靠**远距重复**：关键 token（时刻 / 日期 / 物件 / 昵称）
    在相隔很远的两个镜头里再次出现，就是一次"埋点→回收"。
    为什么不用"反转词计数"：台词措辞追不上（初版用"没想到/原来/
    真相"词表，两个好示例被打成 0/3 幕与 1/3 幕，而失静不报错）。
    回声对写法免疫——"三年前，今天"与"23:47"都能被同一把尺子量到。

    三类伪回声必须排掉，否则任何剧本都满分：
      1. **胶水词**：主角名/主语几乎每镜出现（"男人""店员""周宁"）——
         命中镜数占比超 max_shot_ratio 即弃；
      2. **贴身重复**：相邻镜头复读（"十一号"连说两镜）——
         时间跨度不足 min_span_ratio 即弃；
      3. **世界观词**：场景本身（收银台/货架/屏幕）隔几镜就出现一次，
         是"世界一直在"不是"埋点回收"——要求首次命中在前 45%、
         末次命中在后 45%（前半程埋、后半程收）即弃；
      4. **虚词噪音**："的了吧"组成的候选词——stopchars/stopwords 即弃。
    """
    ec = _tbl(st, "echoes")
    w = int(st.get("w_echoes") or 25)
    min_echoes = int(ec.get("min_echoes") or 3)
    min_span = float(ec.get("min_span_ratio") or 0.25)
    max_ratio = float(ec.get("max_shot_ratio") or 0.30)
    max_hits = int(ec.get("max_hits") or 6)
    merge_jac = float(ec.get("merge_jaccard") or 0.6)
    require_line = bool(ec.get("require_line", False))
    stopchars = set(ec.get("stopchars") or "")
    stopwords = set(ec.get("stopwords") or ())

    n = len(shots)
    total = sum(s["sec"] for s in shots)
    if not n or total <= 0:
        return {"name": "呼应结构", "score": 0.0, "max": w,
                "detail": "无镜头或总时长为 0，不可验", "hits": []}

    # 每镜的候选 token：显式时刻/日期模式 + 子句内滑动 2-4  gram。
    # ★ 不能按标点切完就取整段的前 4 字——"一只手扣死吊环"会被切成
    #   "一只手扣/死吊环"两个残片，"收银台"也会碎成"银台/台前"。
    #   必须先按子句切开，再在子句内滑窗，token 才是人话。
    per_shot, line_tokens, acc = [], set(), 0.0
    for s in shots:
        mid = acc + s["sec"] / 2
        acc += s["sec"]
        body = _shot_text(s)
        toks = set(re.findall(r"\d{1,2}:\d{2}|\d+[号天年前秒分钟小时刻]", body))
        for clause in _CLAUSE_SPLIT.split(body):
            L = len(clause)
            if L < 2:
                continue
            for size in (2, 3, 4):
                if L < size:
                    continue
                for k in range(L - size + 1):
                    toks.add(clause[k:k + size])
        # 台词里出现过的 token：埋点回收在短剧里是"说出来"的（名字/数字/
        # 承诺/物件名），只出现在画面列的重复是布景，不是埋点。
        line = s.get("line") or ""
        ltoks = set(re.findall(r"\d{1,2}:\d{2}|\d+[号天年前秒分钟小时刻]", line))
        for clause in _CLAUSE_SPLIT.split(line):
            L = len(clause)
            for size in (2, 3, 4):
                if L < size:
                    continue
                for k in range(L - size + 1):
                    ltoks.add(clause[k:k + size])
        line_tokens |= ltoks
        per_shot.append((mid, toks))

    positions: dict[str, list] = {}
    for mid, toks in per_shot:
        for t in toks:
            positions.setdefault(t, []).append(mid)

    cands = []
    for t, mids in positions.items():
        if len(mids) < 2 or len(mids) > max_hits:
            continue                       # 只一次：没埋；太多次：复读
        if len(mids) / n > max_ratio:
            continue                       # 胶水词（主角名/主语）
        if any(ch in stopchars for ch in t) or t in stopwords:
            continue                       # 虚词噪音
        if require_line and t not in line_tokens:
            continue                       # 只出现在画面列 = 布景，不是埋点
        first, last = min(mids), max(mids)
        span = (last - first) / total
        if span < min_span:
            continue                       # 贴身重复，不是埋点回收
        # 真呼应是"前半程埋、后半程收"：首次命中不早于前 45% 的不算埋点，
        # 末次命中不晚于后 45% 的不算回收。少了这条，世界观词
        # （收银台/货架/屏幕）每隔几镜出现一次也全算呼应。
        if first > total * 0.45 or last < total * 0.55:
            continue
        cands.append((t, set(mids)))

    # ★ 同源切片合并：滑窗会从"收银台"里切出"收银""银台"，三个词命中
    #   同一批镜头——那是**同一个现象**，不是一个现象算三处。
    #   判据：命中位置集合的 Jaccard 相似度 ≥ merge_jaccard 即归为一簇，
    #   簇内取最长 token 作代表（最具体：收银台 > 收银 > 银台）。
    #   实测未合并时，一个便利店示例被数成 28 处呼应，全绿失真。
    cands.sort(key=lambda x: -len(x[1]))
    clusters: list[dict] = []
    for t, mids in cands:
        for cl in clusters:
            union = cl["mids"] | mids
            jac = len(cl["mids"] & mids) / len(union) if union else 0.0
            if jac >= merge_jac:
                cl["mids"] = union
                if len(t) > len(cl["rep"]):
                    cl["rep"] = t          # 代表词取最具体的那个
                cl["toks"].append(t)
                break
        else:
            clusters.append({"rep": t, "mids": set(mids), "toks": [t]})

    echoes = []
    for cl in clusters:
        mids = cl["mids"]
        span = (max(mids) - min(mids)) / total
        echoes.append((cl["rep"], len(mids), round(span, 2)))

    echoes.sort(key=lambda x: -x[1])
    got = len(echoes)
    sc = w if got >= min_echoes else w * got / min_echoes
    return {"name": "呼应结构", "score": round(sc, 1), "max": w,
            "detail": f"{got} 处远距呼应（满配 {min_echoes} 处）："
                      + ("、".join(f"{t}×{c}" for t, c, _ in echoes[:6]) if echoes
                         else "未识别到埋点回收"),
            "hits": echoes[:8]}


def dim_places(shots: list[dict], text: str, st: dict) -> dict:
    """空间变化：场景一直在换，戏才往前走。一个客厅说完全场是棚拍广播剧。

    ★ 只从**镜头列**（画面/台词）取场景名，不扫正文。
      初版扫全文，于是正文"场景：客厅、卧室、厨房"这种清单就能拿满分——
      实测一张 8 镜全在客厅的表，正文列三个场景名，空间变化直接 20/20。
      这就是"按名词查表"的又一次翻车：查到了词，却不是这个维度要的东西。
      空间变化量的是"这一镜发生在哪儿"，所以必须逐镜看。

    ★ **位置词不能进场景表**（v0.6.1 修正）。"门口""门前""床边""窗前""工位"
      是"某空间内的一处"，不是另一个空间——楼道里的门前还是楼道。
      收了它们，同一处戏会被数成两三处场景。实测《同意》只有楼道/卧室/电梯
      三处，被数成五处（床边/楼道/电梯/走廊/门前）。
      **层级：家具 < 位置 < 空间。判据只认空间**，位置写进"动作一句话"即可。

      注意：位置词清出词表后，**一镜可以命中多个空间词**（镜头从电梯摇到
      走廊，两个都是真空间），不能再做"一镜一票"——那会把真空间也合并掉
      （实测《十七楼》因此从 2 处掉到 1 处）。
    """
    pl = _tbl(st, "places")
    words = _words(pl)
    w = int(st.get("w_places") or 20)
    # 逐镜取该镜出现的场景词 —— 命中才算"这一镜在这个空间"
    per_shot = []
    for s in shots:
        t = _shot_text(s)
        hit = _hits(t, words)
        # 同源合并：词表里"收银台"与"收银"会同时命中同一场戏——一个地方算两处。
        # 被别的命中词包含的短词直接丢弃（保留最具体的那个）。
        hit = {x for x in hit if not any(x != y and x in y for y in hit)}
        if hit:
            per_shot.append(sorted(hit))
    found = set()
    for h in per_shot:
        found |= set(h)
    n = len(found)
    full = int(pl.get("full_count") or 3)
    sc = w if n >= full else (w / 2 if n == full - 1 else 0.0)
    detail = f"{n} 处场景（满配 {full} 处）：" + (
        "、".join(sorted(found)[:8]) if found else "未识别")
    if not found:
        detail += "｜分镜表里没写出场景名，戏可能一直在一个地方"
    return {"name": "空间变化", "score": round(sc, 1), "max": w,
            "detail": detail, "hits": sorted(found)[:8]}


def dim_pointing(shots: list[dict], st: dict) -> dict:
    """点题自律：好短剧演出来，不说出来。

    叙述手段（旁白/字幕）+ 主题句同时出现才算点题——只有"字幕：第三天"
    不算，那是信息标注；"（旁白）后来才明白，这就是生活"才算。
    """
    pt = _tbl(st, "pointing")
    marks = tuple(pt.get("marks") or ())
    themes = tuple(pt.get("themes") or ())
    w = int(st.get("w_pointing") or 15)
    pen = float(pt.get("penalty") or 5)
    hits = []
    for s in shots:
        line = s.get("line") or ""
        if any(m in line for m in marks) and any(t in line for t in themes):
            hits.append({"no": s["no"], "line": line[:30]})
    sc = max(0.0, w - pen * len(hits))
    return {"name": "点题自律", "score": round(sc, 1), "max": w,
            "detail": (f"{len(hits)} 处旁白/字幕点题（每处扣 {pen:g} 分）"
                       if hits else "无旁白点题"),
            "hits": hits[:4]}


def dim_conflict(shots: list[dict], st: dict) -> dict:
    """冲突面：两个以上人物 + 对抗动作。冲突得落到动作上，光嘴上说不算。

    7 列工作表没有"谁在画面"列 → 人物数不可验：该半分直接不得，
    并在 detail 里注明，不把"画面里没人名"当成"只有一个角色"。
    """
    cf = _tbl(st, "conflict")
    words = _words(cf)
    w = int(st.get("w_conflict") or 15)
    min_ch = int(cf.get("min_characters") or 2)
    min_ct = int(cf.get("min_confront") or 2)
    ch_sc = float(cf.get("char_score") if cf.get("char_score") is not None else 8)
    ct_sc = float(cf.get("confront_score") if cf.get("confront_score") is not None else 7)

    chars, who_verifiable = set(), False
    for s in shots:
        names = _who_names(s)
        if names:
            who_verifiable = True
        chars |= names
    confront = [s for s in shots if _hits(_shot_text(s), words)]

    sc = (ch_sc if len(chars) >= min_ch else 0.0) + \
         (ct_sc if len(confront) >= min_ct else 0.0)
    det = f"{len(chars)} 个人物（需 ≥{min_ch}）｜{len(confront)} 镜对抗动作（需 ≥{min_ct}）"
    if not who_verifiable:
        det += "｜人物数不可验（本表无「谁在画面」列），该半分未给"
    return {"name": "冲突面", "score": round(sc, 1), "max": w,
            "detail": det,
            "hits": sorted(_hits(" ".join(_shot_text(s) for s in confront), words))[:6]}


# ------------------------------------------------------------
# 汇总
# ------------------------------------------------------------

def score(shots: list[dict], text: str = "", d: dict | None = None) -> dict:
    """对一份分镜表打故事质量分。返回总分 / 档位 / 五维明细。"""
    drama = _drama(d)
    st = drama.get("story") if isinstance(drama.get("story"), dict) else ST_
    st = st or ST_
    if not shots:
        return {"score": 0, "band": "回炉", "dims": [],
                "note": "未解析到任何镜头"}

    dims = [dim_events(shots, st),
            dim_echoes(shots, st),
            dim_places(shots, text, st),
            dim_pointing(shots, st),
            dim_conflict(shots, st)]
    total = sum(x["score"] for x in dims)
    strong = float(st.get("band_strong") or 80)
    weak = float(st.get("band_weak") or 60)
    band = "强" if total >= strong else ("可打磨" if total >= weak else "回炉")
    return {"score": round(total, 1), "band": band, "dims": dims,
            "note": "结构特征参考分，不替代 scan 的镜头语言判据"}


# ------------------------------------------------------------
# 输出
# ------------------------------------------------------------

def render(res: dict, title: str = "") -> str:
    band_color = {"强": C.ok, "可打磨": C.warn, "回炉": C.err}
    out = [C.bold(f"\n  短剧故事质量 · {title or '(未命名)'}"), "  " + "─" * 56]
    out.append(f"  总分 {res['score']:g}   "
               + band_color.get(res["band"], C.dim)(res["band"])
               + C.dim(f"  （满分 100；≥{ST_.get('band_strong', 80)} 强，"
                       f"<{ST_.get('band_weak', 60)} 回炉）"))
    out.append("")
    for x in res["dims"]:
        bar_full = 20
        filled = int(round(x["score"] / x["max"] * bar_full)) if x["max"] else 0
        bar = "█" * filled + "░" * (bar_full - filled)
        out.append(f"  {x['name']:<6} {x['score']:>5g}/{x['max']:<3g} {bar}")
        out.append(C.dim(f"         {x['detail']}"))
    out.append("")
    out.append(C.dim(f"  {res['note']}"))
    out.append("")
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    pos = [a for a in args if not a.startswith("--")]
    opt = {a: (args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else True)
           for i, a in enumerate(args) if a.startswith("--")}
    if not pos:
        print("用法: python quality.py <分镜表.md> [--json] [--mode ai|live] [--form 形态]")
        return 1
    path = Path(pos[0])
    if not path.exists():
        print(C.err(f"文件不存在: {path}"))
        return 1
    import scan as SC
    from common import find_project_root
    text = path.read_text(encoding="utf-8")
    shots = SC.parse_shotlist(text)
    proj = find_project_root(path)
    const = constants_for(proj, opt.get("--mode"),
                          form=opt.get("--form")) if proj else constants_for(
        None, opt.get("--mode"), form=opt.get("--form"))
    res = score(shots, text, const["drama"])
    print(json.dumps(res, ensure_ascii=False, indent=2) if opt.get("--json")
          else render(res, path.stem))
    return 0


if __name__ == "__main__":
    sys.exit(main())
