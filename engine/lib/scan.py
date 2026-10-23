# -*- coding: utf-8 -*-
"""分镜表质量扫描（scan）

**为什么不能复用 prose_scan**：prose_scan 打的是"AI 味六层 + 句长 + 感官密度"，
会把短剧往文学化、长句、细腻描写推，正好是短剧的死因。短剧要的是钩子密度、
时长控制、镜头语言克制、对白占比。本模块按短剧的尺子量。

判据与阈值见 `references/shot.md` 与 `config/dramakit.toml`。
镜头语言分两档：`ai`（AI 生成，单镜 3-4 秒最稳）与 `live`（真人实拍，长镜省机位）。
**两档判据方向相反，不能共用一把尺子**——见 `resolve_profile()`。
纯标准库，零 token。

用法：
    python engine/lib/scan.py <分镜表.md|.csv> [--json] [--format single|series] [--mode ai|live]
"""
import csv
import io
import json
import re
import sys
from collections import Counter
from pathlib import Path

from common import (CONST, C, apply_form_profile, bar, compliance_for,
                    constants_for, is_row, model_for, norm_head, normalize_form,
                    platform_for, sizes_for, split_row)

D_ = CONST["drama"]
DS_ = D_["single"]
DSER_ = D_["series"]
DSHOT_ = D_["shot"]
DQ_ = D_["quality"]

# 台词里的钩子信号：疑问 / 否定 / 冲突
HOOK_LINE = ("？", "?", "凭什么", "为什么", "难道", "竟然", "居然", "没想到",
             "其实", "真相", "秘密", "骗", "撒谎", "闭嘴", "滚", "你敢",
             "分手", "离婚", "后悔")
# 画面里的事件信号：突发 / 动作转折 / 关键道具。
# ★ 词表要收"实际会写出来的说法"。只收"推门"而剧本写"门被推开"，
#   判据就整条失效——而失效率不会报错，只会表现为"钩子断档"误报。
HOOK_VISUAL = ("震动", "来电", "消息", "响起", "铃声", "突然", "瞬间", "推门",
               "门被推开", "起身", "站起", "跪", "耳光", "摔", "撕", "转身走", "拉住",
               "抱住", "吻", "拥抱", "亮起", "闪", "空无一人", "空了", "没人",
               "湿透", "攥紧", "停在", "没动", "回头", "低头看")
# 情绪推进型运镜才算钩子信号；缓拉/跟移/跟摇是收尾语言，不算
HOOK_MOVES = ("缓推", "推", "仰拍", "俯拍", "摇")

CLOSE_SIZES = ("特写", "大特写")

FORMS = ("live_action", "anime", "motion_comic", "static_comic")

# 漫画景别与运镜白名单（兜底，实际以 [panel].sizes / .moves 为准）：
# 不在表内的说明写的是影视景别，模型会抽错提示词
COMIC_SIZES = ("大格", "中格", "特写格", "远景格", "插入格")
COMIC_MOVES = ("固定", "推", "拉", "摇", "移")

# 哪些形态用"格"不用影视景别——**只有两种漫**（静态帧）。
# 动漫短剧是连续动画，用影视景别（近景/中景/特写），不算"格"形态。
# 这些形态的近中景占比必须按漫画景别算，否则 size_dist 里没有"近景/中景"
# 两个键，cm_ratio 恒为 0，**每次扫描都会误报"近中景占比偏低"**
# （实测：漫剧样本 24 格全判 0%）。
PANEL_FORMS = ("motion_comic", "static_comic")
STATIC_FORMS = ("static_comic",)


def near_medium_set(d: dict) -> tuple:
    """本形态下"该占多数"的景别集合（用于近中景占比判据）。

    视频形态与旧版一致：近景 + 中景（**不含特写**——连用特写观众失去空间感）。
    漫画形态用格：大格 / 中格 / 特写格。
    """
    if d.get("form") in PANEL_FORMS:
        panel = d.get("panel") or {}
        return tuple(panel.get("close_sizes") or COMIC_SIZES)
    return ("近景", "中景")


def reaction_set(d: dict) -> tuple:
    """本形态下的"情绪特写"景别集合（用于反应镜时长判据）。

    与 near_medium_set **是两个不同的集合**，别混：
      - 近中景管"观众看得清脸"，含近景
      - 反应镜管"纯画面情绪格有多长"，只含特写级
    视频形态：特写 / 大特写。漫画形态：特写格。
    """
    if d.get("form") in PANEL_FORMS:
        return ("特写格", "大格")
    return CLOSE_SIZES



def resolve_profile(d: dict, mode: str = "ai", form: str | None = None) -> tuple:
    """从 drama 表里取判据表 → (shot, single, quality, panel, asset)。

    `[shot] / [single] / [quality]` 是**全局视频基线**；
    各形态（anime / motion_comic / static_comic）只写与基线的差异项，
    按表名分组逐表覆盖。

    形态与档位的应用发生在 `common.apply_form_profile`——那里保证了
    「项目覆盖 > 形态 > 档位 > 全局基线」的优先级。本函数只负责读取：

    - `d` 带 `form` 标记 → 上游已算好，直接用；此时**忽略 mode/form 参数**，
      因为带标记的常数已经合过项目覆盖，优先级高于命令行档位。
    - `d` 没有标记（调用方直接传了 CONST）→ 按 mode/form 现场应用一次。

    `panel` / `asset` 只在有"格"的形态下非空；视频形态拿到 `{}`，
    下游一律 `(d.get("panel") or {})` 取用，不直接下标。
    """
    if "form" not in d:
        d = apply_form_profile({"drama": d}, form or "live_action", mode)["drama"]
    return (dict(d["shot"]), dict(d["single"]), dict(d["quality"]),
            dict(d.get("panel") or {}), dict(d.get("asset") or {}))




def find_project_root(path: "Path | str", levels: int = 4) -> "Path | None":
    """从分镜表文件向上找项目根：含 config/overrides.toml 或 project.json 的目录。

    短剧项目复用小说的项目骨架，项目级阈值走 <项目>/config/overrides.toml。
    分镜表常放在 shots/ 子目录下，故需要上溯若干层；找到即返回，否则 None。
    没有它，`drama scan` 就只会用全局基线——1 分钟的短剧会被 140 秒的尺子误报。
    """
    d = Path(path).resolve().parent
    for _ in range(levels + 1):
        if (d / "config" / "overrides.toml").exists() or (d / "project.json").exists():
            return d
        if d.parent == d:
            break
        d = d.parent
    return None


def _norm_sec(v) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else 0.0


# ★ 全角竖线 ｜ 与半角 | 等价、列名末尾括注要剥掉——两条都在 common 里，
#   因为交付校验（shotlist）与解析（scan）必须用同一套切法，
#   两边各写一份就会各自漂移，最后一张表两种读法。
#   踩过的坑：只认半角时，老板模板格式的表**解析成空列表**且不报错，
#   scan 拿到 0 个镜头，全部判据"通过"。


# 表头 → 字段。**按表头解析，不按列数**。
# 踩过的坑：早先按列数分派（6 列=交付、7 列=工作），骨架表一加"开场站位"
# 就变成 7 列，整张交付表被当成工作格式解析——列错位、判据全废，且不报错。
# 加列是常态，列数不是身份，表头才是。
_HEAD = {
    "镜": "no", "镜号": "no",
    "时长": "sec", "目标时长": "sec", "秒": "sec",
    "景别": "size",
    "运镜": "move", "镜头": "move", "机位": "move",
    "谁在画面": "who", "人物": "who",
    "开场站位": "staging", "站位": "staging",
    "动作一句话": "action", "动作": "action",
    "画面": "visual", "画面一句话": "visual",
    "台词原文": "line", "台词": "line", "对白": "line", "气泡对白": "line",
    "声音": "sound", "音效": "sound",
    "英文提示词": "prompt_en", "提示词": "prompt_en",
}


def _assemble(rec: dict) -> dict:
    """把按表头取到的原始单元格拼成统一的镜头 dict。

    visual 的语义是"这一镜看得见什么"，交付表把它拆成「谁在画面 + 动作」
    两列，工作表则直接给一列「画面」。两种写法都要归到同一个字段上，
    否则下游（quality 的 _who_names、scan 的词表）就得各写一套分支。
    """
    who = rec.get("who", "")
    if "visual" in rec:
        visual = rec["visual"]
        if who:
            visual = f"{who}｜{visual}"
    else:
        visual = "｜".join(x for x in (who, rec.get("action", "")) if x)
    shot = {
        "no": int(rec["no"]), "sec": _norm_sec(rec.get("sec", 0)),
        "size": rec.get("size", ""), "move": rec.get("move", "固定"),
        "visual": visual, "line": rec.get("line", ""),
        "sound": rec.get("sound", ""),
        "prompt_en": rec.get("prompt_en", ""),
    }
    # staging 只在表里真有这一列时才带上——交付稿没有这一列，
    # 凭空塞一个空字符串会让"覆盖率"把交付稿也算成"站位全没填"。
    if "staging" in rec:
        shot["staging"] = rec["staging"]
    return shot


def parse_shotlist(text: str) -> list[dict]:
    """解析主分镜表：md 表格或 csv（8 列，末列英文提示词）。

    md 走**表头映射**；表头认不出来才退回列数分派，保住老文件的兼容。
    """
    stripped = text.lstrip("\ufeff")
    shots = []

    # ★ 判"是不是 md 表格"不能只看半角 |。老板模板整篇没有外框竖线，
    #   只看 | 会把它当 CSV 送去 csv.reader，然后一行都解析不出来。
    if not any(is_row(l) for l in stripped.splitlines()):
        rows = [r for r in csv.reader(io.StringIO(stripped)) if r and len(r) >= 7]
        for r in rows:
            if not r[0].strip().isdigit():
                continue
            shots.append({
                "no": int(r[0]), "sec": _norm_sec(r[1]),
                "size": r[2].strip(), "move": r[3].strip(),
                "visual": r[4].strip(), "line": r[5].strip(),
                "sound": r[6].strip(),
                "prompt_en": r[7].strip() if len(r) > 7 else "",
            })
        return shots

    header, raw = None, []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        # 表格行：半角 | 开头，或者含全角 ｜（老板模板不带外框竖线）
        if not (line.startswith("|") or "｜" in line):
            continue
        cells = split_row(line)
        if len(cells) < 2:
            continue
        if header is None:
            if norm_head(cells[0]) in ("镜", "镜号"):
                header = [norm_head(c) for c in cells]
            continue
        if not cells[0].isdigit():
            continue
        raw.append(cells)

    # 表头映射成功（至少认出镜号与时长）就走映射；否则退回列数分派
    if header and sum(1 for h in header if h in _HEAD) >= 4:
        for cells in raw:
            rec = {}
            for h, c in zip(header, cells):
                k = _HEAD.get(h)
                if k and k not in rec:
                    rec[k] = c
            if "no" in rec:
                shots.append(_assemble(rec))
        return shots

    for cells in raw:
        if len(cells) == 7:
            # 工作格式：镜|时长|景别|运镜|画面|台词|声音
            shots.append({
                "no": int(cells[0]), "sec": _norm_sec(cells[1]),
                "size": cells[2], "move": cells[3],
                "visual": cells[4], "line": cells[5], "sound": cells[6],
                "prompt_en": "",
            })
        elif len(cells) == 6:
            # 交付格式：镜|时长|景别|谁在画面|动作一句话|台词原文
            # 没有运镜与声音两列，按"固定镜头、无特殊音效"处理——
            # 交付表是给人看和拍的，不是给模型抽提示词的，少这两列是有意的。
            shots.append({
                "no": int(cells[0]), "sec": _norm_sec(cells[1]),
                "size": cells[2], "move": "固定",
                "visual": f"{cells[3]}｜{cells[4]}", "line": cells[5],
                "sound": "", "prompt_en": "",
            })
    return shots


def parse_acts(text: str) -> list[dict]:
    """从 md 末尾的「时长核对」表解析分幕：幕名 / 镜号区间 / 表内标注时长。"""
    acts = []
    pat = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(\d+)\s*-\s*(\d+)\s*\|\s*(\d+)\s*s\s*\|", re.M)
    for m in pat.finditer(text):
        name = m.group(1).strip()
        if "合计" in name:
            continue
        acts.append({"name": name, "lo": int(m.group(2)),
                     "hi": int(m.group(3)), "declared": int(m.group(4))})
    return acts


def has_line(s: dict) -> bool:
    """该镜是否有**台词**。

    排除两种占位：
      - "无"           = 这一镜没有台词
      - "—" / "—（…）" = 没有台词（破折号后跟的是画面文字说明，不是台词）

    ★ "—（画面文字：电梯停在十七楼）"这类**不是台词但有内容**——
      它要算 has_content（长镜不空），但不算 has_line（有台词）。
      这两件事分开判：初版 has_content 只判 `t != "无"`，于是单独的"—"
      也被当成有内容，**长镜空镜判据整条失效且不报错**（实测：12 个 6 秒
      长镜全填"—"，一个都没报空镜）。这是本项目最典型的病——判据静默失灵。
    """
    t = (s.get("line") or "").strip()
    if not t or t == "无":
        return False
    if t.startswith("—") or t.startswith("-"):
        return False
    return True


def has_content(s: dict) -> bool:
    """该镜是否承载内容（台词 / 字幕 / 旁白 / 画面文字）。长镜不得是纯空镜。

    "—（画面文字：…）"算有内容——它是画面上钉住的信息，正是长镜该干的事。
    单独的"—"不算——那等于什么都没说。
    """
    t = (s.get("line") or "").strip()
    if not t or t == "无":
        return False
    if t.startswith("—") or t.startswith("-"):
        # 破折号后跟了实质说明（画面文字/字幕）→ 有内容
        rest = t.lstrip("—-").strip()
        return bool(rest) and rest not in ("（）", "()")
    return True


def is_hook(s: dict, close_sizes: tuple = CLOSE_SIZES,
             long_take_sec: float | None = None) -> bool:
    """启发式钩子信号：情绪运镜 / 情绪特写 / 冲突台词 / 突发事件 / 信息密集长镜。

    ★ `close_sizes` 必须按形态传入。写死"特写/大特写"会让漫剧的"特写格/大格"
    一个都认不出——**漫剧形态下钩子判据会整体失效，表现为所有窗口都"断档"**
    （实测：《便利店》24 格全判无钩子）。

    ★ `long_take_sec` 同理，必须按形态/档位传入。写死全局值（5s）会让
      漫剧的 4s 长镜（漫剧 long_take_sec=4）判不出钩子信号——**形态差异
      只在配置里生效，判据里却看不到，等于那半张表是空转的**。
    """
    if any(m in (s["move"] or "") for m in HOOK_MOVES):
        return True
    if s["size"] in close_sizes:
        return True
    if any(w in (s.get("line") or "") for w in HOOK_LINE):
        return True
    if any(w in (s.get("visual") or "") for w in HOOK_VISUAL):
        return True
    lt = DSHOT_["long_take_sec"] if long_take_sec is None else long_take_sec
    if s["sec"] >= lt:
        return True
    return False



def has_ending_hook(s: dict) -> bool:
    """连载结尾必须留悬念：台词含悬念词，或画面含突发事件。

    不能复用 is_hook —— 长镜与情绪特写都算钩子信号，收尾镜会误判成有钩子。
    """
    if any(w in (s.get("line") or "") for w in HOOK_LINE):
        return True
    if any(w in (s.get("visual") or "") for w in HOOK_VISUAL):
        return True
    return False


def hook_windows(shots: list[dict], window_s: int,
                 close_sizes: tuple = CLOSE_SIZES,
                 long_take_sec: float | None = None) -> list[dict]:
    """按累计时长切窗口，返回每个窗口及其是否含钩子。"""
    wins, cur, t0, acc = [], [], 0.0, 0.0
    for s in shots:
        cur.append(s)
        acc += s["sec"]
        if acc >= window_s:
            wins.append({"t0": round(t0, 1), "t1": round(t0 + acc, 1),
                         "shots": cur,
                         "hook": any(is_hook(x, close_sizes, long_take_sec) for x in cur)})
            t0 += acc
            acc, cur = 0.0, []
    if cur:
        wins.append({"t0": round(t0, 1), "t1": round(t0 + acc, 1),
                     "shots": cur,
                     "hook": any(is_hook(x, close_sizes, long_take_sec) for x in cur)})
    return wins


def hook_anchors(d: dict) -> list:
    """秒级锚点表（config [hook].anchors），形态/档位/项目覆盖后取值。"""
    h = d.get("hook") or {}
    anchors = [a for a in (h.get("anchors") or []) if isinstance(a, (int, float))]
    return sorted(float(a) for a in anchors)


def fmt_anchors(anchors: list) -> str:
    """锚点秒表的人类可读形式（0/30/60/…/180s）。空表给占位，不返回空串。"""
    if not anchors:
        return "（未配置）"
    return "/".join(f"{a:g}" for a in anchors) + "s"


def hook_anchor_checks(shots: list[dict], d: dict,
                       close_sizes: tuple = CLOSE_SIZES) -> tuple[list, list]:
    """秒级锚点核验：该炸的秒数上必须炸（比均匀窗口更接近爆款节奏）。

    两条硬规则：
      1. **锚点 0 = 对抗开场**：第一镜自己就得是钩子。铺垫式开场在第一秒
         就被划走，这是唯一没有容差的锚点。
      2. 其余锚点 t：到第 t 秒为止，最后一个钩子距 t 不得超过 anchor_gap_s；
         从未出现过钩子也算未命中。

    超出总时长的锚点**自动忽略**——60 秒的片子不查 180s 锚点，
    为够不着的秒数误报是把尺子用反。

    ★ 长镜阈值取**形态/档位解析后的值**：漫剧 long_take_sec=4，
      写死全局 5s 会让漫剧的 4s 长镜判不出钩子信号，形态差异形同虚设。

    返回 (未命中锚点列表, 全部锚点明细)。anchor_required = false 时直接放行。
    """
    h = d.get("hook") or {}
    if h.get("anchor_required") is False:
        return [], []
    anchors = hook_anchors(d)
    if not anchors:
        return [], []
    gap = float(h.get("anchor_gap_s") or 15)
    long_take = float((d.get("shot") or {}).get("long_take_sec")
                      or DSHOT_["long_take_sec"])

    total = sum(s["sec"] for s in shots)
    ends, acc = [], 0.0
    for s in shots:
        acc += s["sec"]
        if is_hook(s, close_sizes, long_take):
            ends.append(round(acc, 2))

    detail, miss = [], []
    for a in anchors:
        if a > total + 1e-9:
            continue                      # 够不着的锚点不查
        if a == 0:
            ok = bool(shots) and is_hook(shots[0], close_sizes, long_take)
            last = 0.0 if ok else None
        else:
            last = max((e for e in ends if e <= a + 1e-9), default=None)
            ok = last is not None and (a - last) <= gap + 1e-9
        detail.append({"anchor": a, "ok": ok,
                       "dist": None if last is None else round(a - last, 1)})
        if not ok:
            miss.append(a)
    return miss, detail


def is_comic(d: dict) -> bool:
    return d.get("form") in PANEL_FORMS


def comic_panel_checks(shots: list[dict], d: dict) -> tuple[list, list, dict]:
    """漫剧 / 静态漫专属判据。视频形态一律返回空——这些检查对视频短剧毫无意义。

    六类，全部确定性可算：
      1. **气泡对白字数**：超上限就是装不下，物理问题不是风格问题
      2. **景别 / 运镜白名单**：写影视景别（近景/中景）模型会抽错提示词，
         漫画只有"格"
      3. **静态漫不许有运镜**：写了推拉就是形态写错了，出片时执行不了
      4. **格组起承转合**：一组格子数越界说明节拍粒度失控
      5. **参考图配额**：单格引用的参考图超过配额，说明在自由发挥，脸会漂
      6. **近景占比**：大格/中格/特写格得占多数，否则读不清脸

    ★ 第 6 项踩过"按名词查表"的坑（v0.6.0 修正）：同一个语义在两种形态下
      叫不同名字——视频形态（在 `[*.quality]`）叫 `close_medium_ratio_min`，
      漫画形态（在 `[*.panel]`）叫 `close_ratio_min`。代码只认了视频那个名字，
      漫画的阈值从定下来就没人读。**别假设同一个概念只有一个名字。**

    第 5 项依赖分镜表里有「参考图」列；交付表（6 列）没有这列，
    所以只在工作格式（7 列）下可查，缺列时明确记 `asset_unverifiable`
    ——**不能静默当成通过**。
    """
    blocking, warning, info = [], [], {}
    if d.get("form") not in PANEL_FORMS:
        return blocking, warning, info
    panel = d.get("panel") or {}
    asset = d.get("asset") or {}
    if not panel:
        return blocking, warning, info
    frm = d.get("form")

    cap = panel.get("bubble_line_max")
    if cap:
        over = [s for s in shots
                if has_line(s) and len(re.sub(r"[\s，。！？、…—～·]", "",
                                              s["line"] or "")) > cap]
        if over:
            blocking.append({
                "check": "气泡对白超长",
                "value": "镜 " + "/".join(f"{s['no']}({len(s['line'])}字)" for s in over),
                "limit": f"气泡最多 {cap} 字，超了装不下",
            })

    sizes = panel.get("sizes") or list(COMIC_SIZES)
    moves = panel.get("moves") or list(COMIC_MOVES)
    bad_size = [s for s in shots if s["size"] not in sizes]
    if bad_size:
        warning.append({
            "check": "景别不在漫画白名单",
            "value": "镜 " + "/".join(f"{s['no']}({s['size']})" for s in bad_size),
            "limit": "、".join(sizes),
        })
    bad_move = [s for s in shots if s["move"] not in moves]
    if bad_move:
        # 静态漫写了运镜不是"警告"是"形态写错"——它根本没法执行，阻断级
        level = blocking if frm in STATIC_FORMS else warning
        level.append({
            "check": "静态漫出现运镜" if frm in STATIC_FORMS else "运镜不在漫画白名单",
            "value": "镜 " + "/".join(f"{s['no']}({s['move']})" for s in bad_move),
            "limit": "、".join(moves) if frm not in STATIC_FORMS
                     else "静态漫只能固定，无运镜",
        })

    gmin, gmax = panel.get("grid_min", 4), panel.get("grid_max", 8)
    n = len(shots)
    if gmin and gmax:
        # 按格组粗查：总格数应能整分为若干组，且每组落在区间内
        if gmin <= n <= gmax * 8:
            groups = max(1, round(n / ((gmin + gmax) / 2)))
            per = n / groups
            if per < gmin or per > gmax:
                warning.append({
                    "check": "格组粒度失控",
                    "value": f"共 {n} 格，按 {groups} 组均分每组 {per:.1f} 格",
                    "limit": f"每组 {gmin}-{gmax} 格（起承转合）",
                })
    info["grid_range"] = (gmin, gmax)
    info["grids"] = n
    info["bubble_cap"] = cap
    info["reading_order"] = panel.get("reading_order", "")

    # 6. **漫画近景占比**：漫画要"读得清"，大格/中格/特写格得占多数。
    #    ★ 这里踩过"按名词查表"的坑：同一个语义（近景占比下限）在两种形态下
    #    叫不同名字——视频形态叫 close_medium_ratio_min（在 [*.quality]），
    #    漫画形态叫 close_ratio_min（在 [*.panel]）。代码只认了视频那个名字，
    #    于是漫画的阈值从定下来就没人读。
    #    景别集合也照形态取：漫画用"格"，不能用影视景别算。
    crm = panel.get("close_ratio_min")
    if crm and shots:
        cs = set(panel.get("close_sizes") or CLOSE_SIZES)
        nclose = sum(1 for s in shots if s["size"] in cs)
        ratio = nclose / len(shots)
        info["close_ratio"] = round(ratio, 3)
        info["close_ratio_min"] = float(crm)
        if ratio < float(crm):
            warning.append({
                "check": "漫画近景占比不足",
                "value": f"{ratio * 100:.0f}%（{nclose}/{len(shots)} 格）",
                "limit": f"≥ {float(crm) * 100:.0f}%，否则读不清脸",
            })

    # 参考图配额：只有工作格式（带 prompt_en 列）才查得到
    quota = asset.get("ref_per_panel_max") or asset.get("ref_per_shot_max")
    if quota:
        refs = [s for s in shots if (s.get("prompt_en") or "").count("ref:") > quota]
        if refs:
            warning.append({
                "check": "单格参考图超配额",
                "value": "镜 " + "/".join(str(s["no"]) for s in refs),
                "limit": f"每格最多 {quota} 张（超出=自由发挥，脸会漂）",
            })
        else:
            info["ref_quota_ok"] = True

    if asset.get("scene_bible_required"):
        info["scene_bible_required"] = True   # 由 check_delivery 查场景表
    if asset.get("color_sheet_required"):
        info["color_sheet_required"] = True
    if asset.get("style_anchor_required"):
        info["style_anchor_required"] = True
    return blocking, warning, info


# ------------------------------------------------------------
# 合规：发布前必过，机械可判的三件
# ------------------------------------------------------------

def compliance_checks(text: str, shots: list[dict], d: dict,
                      form: str, is_ai: bool = True) -> tuple[list, list, dict]:
    """合规检查：AIGC 标识 / 未成年人题材 / 敏感与违规表达。

    三条都**只判"有没有触碰"**，具体怎么改交人——判据不该假装懂内容。
      - **AIGC 标识**：AI 生成内容必须显式标识（国内发布硬要求）。
        真人实拍档可关（`--live-shoot`），AI 生成必查。
      - **未成年人题材**：命中术语即提示"不拍受害细节"，不判对错。
      - **违规词**：命中即阻断（政策红线）；敏感词只警告（可能是误报）。
    """
    comp = compliance_for(d and {"drama": d} or None)
    blocking, warning, info = [], [], {}
    body = text or ""

    # 1) AIGC 标识
    if comp.get("aigc_label_required") and is_ai:
        label = str(comp.get("aigc_label_text") or "AI 生成")
        key = label[:6]
        if key and key not in body and "AI 生成" not in body and "人工智能" not in body:
            blocking.append({
                "check": "缺 AIGC 标识",
                "value": "交付稿中未见 AI 生成合成标识",
                "limit": f"必须有标识（如「{label}」），国内发布硬要求",
            })
        else:
            info["aigc_labeled"] = True

    # 2) 未成年人题材
    if comp.get("minors_protection"):
        hits = [t for t in (comp.get("minors_terms") or []) if t in body]
        if hits:
            warning.append({
                "check": "涉未成年人题材",
                "value": "、".join(sorted(set(hits))),
                "limit": "不拍受害细节、不给加害过程；只判触碰，写法交人",
            })

    # 3) 违规 / 敏感
    forb = [t for t in (comp.get("forbidden_terms") or []) if t in body]
    if forb:
        blocking.append({
            "check": "命中违规词",
            "value": "、".join(sorted(set(forb))),
            "limit": "政策红线，必须删改",
        })
    sens = [t for t in (comp.get("sensitive_terms") or []) if t in body]
    if sens:
        warning.append({
            "check": "命中敏感表达（可能是误报）",
            "value": "、".join(sorted(set(sens))),
            "limit": "影射真实主体 / 具体自伤手段 / 平台黑话，确认后保留或删改",
        })
    return blocking, warning, info


# ------------------------------------------------------------
# 模型能力：超限必须拒绝，不得截断后调上游
# ------------------------------------------------------------

def model_checks(shots: list[dict], d: dict, model: str | None = None) -> tuple[list, list, dict]:
    """按生成后端能力核验镜头表，**超限即阻断**。

    "把一个超长的镜头悄悄拆短再发上游"是本项目要防的事：模型吐不出来就是
    吐不出来，让上游的不确定性静默变成下游的错误结论，是最贵的错。

    查三样（都能从分镜表机械判定），每一样都挂上失败模式分类
    （config [failure.modes]）——不只说"超了"，还说"这会以什么形态失败、
    该怎么修"：
      - **单镜时长超上限** → 失败模式「时长超」
      - **参考图张数超额度** → 失败模式「参考图超」
      - **提示词字数超上限**（仅在能从 prompt_en 列估长时查）→ 失败模式「字数超」

    「审核」「网络」两类失败事前不可完全预判（detectable = false），
    不进阻断，只在 info 里列出，让人知道它们出现了别怪剧本。
    """
    blocking, warning, info = [], [], {}
    m = model_for({"drama": d}, model) if (shots and d) else model_for(None, model)
    if not m:
        return blocking, warning, info
    modes = ((d or {}).get("failure") or {}).get("modes") or {}

    def _mode(name: str) -> dict:
        mo = modes.get(name) or {}
        return {"failure": name,
                "fix": mo.get("fix", ""),
                "trigger": mo.get("trigger", "")}

    max_sec = m.get("max_shot_sec")
    if max_sec:
        over = [s for s in shots if s["sec"] > max_sec]
        if over:
            blocking.append({
                "check": "单镜超模型时长上限",
                "value": "镜 " + "/".join(f"{s['no']}({s['sec']:g}s)" for s in over),
                "limit": f"该后端单镜 ≤ {max_sec}s，超了必须拆镜（不许截断后发）",
                **_mode("时长超"),
            })
        else:
            info["shot_sec_ok"] = True

    ref_max = m.get("ref_images_max")
    if ref_max:
        over_ref = [s for s in shots
                    if (s.get("prompt_en") or "").count("ref:") > ref_max]
        if over_ref:
            warning.append({
                "check": "参考图超模型额度",
                "value": "镜 " + "/".join(str(s["no"]) for s in over_ref),
                "limit": f"该后端单镜最多 {ref_max} 张参考图",
                **_mode("参考图超"),
            })

    # 提示词字数：交付表没有 prompt_en 列，只在能估长时查；估不出就明说
    # "不可验"，不许静默当成通过。
    char_max = m.get("max_prompt_chars")
    if char_max:
        estimable = [s for s in shots if (s.get("prompt_en") or "").strip()]
        if estimable:
            over_p = [s for s in estimable if len(s["prompt_en"]) > char_max]
            if over_p:
                warning.append({
                    "check": "提示词超字数上限",
                    "value": "镜 " + "/".join(
                        f"{s['no']}({len(s['prompt_en'])}字符)" for s in over_p),
                    "limit": f"该后端单镜提示词 ≤ {char_max} 字符",
                    **_mode("字数超"),
                })
        else:
            info["prompt_chars_unverifiable"] = True

    info["model"] = m.get("note", "")
    info["max_shot_sec"] = max_sec
    # 事前不可预判的两类失败：列出来供人对照，不参与判定
    info["undetectable_modes"] = [k for k, v in modes.items()
                                  if v.get("detectable") is False]
    return blocking, warning, info


# ------------------------------------------------------------
# 平台规格：画幅 / 时长档 / 安全区
# ------------------------------------------------------------

def platform_checks(text: str, total_s: float, fmt: str, d: dict,
                    platform: str | None = None) -> tuple[list, list, dict]:
    """平台规格核对：时长档 + 安全区（画幅只能人核，机器判不了构图）。

    - **时长档**：不同平台推荐的单集时长区间不同。60 秒的片投优酷横屏（300s 起）
      就进不了推荐池——这是**可机械核对**的。
    - **安全区**：交付稿里若写了"字幕在画面底部"，而底部 20% 是平台 UI 覆盖区，
      字幕会被盖住——查正文有没有显式声明安全区遵守。
    """
    blocking, warning, info = [], [], {}
    spec = platform_for(d and {"drama": d} or None, platform)
    band = spec.get("duration_band")
    info["platform_aspect"] = spec.get("aspect", "")
    info["platform_band"] = band

    if band and fmt == "single" and total_s:
        lo, hi = float(band[0]), float(band[1])
        if total_s < lo or total_s > hi:
            warning.append({
                "check": "时长不在平台推荐档",
                "value": f"{total_s:.0f}s",
                "limit": f"{spec.get('note', '该平台')} 推荐 {lo:.0f}-{hi:.0f}s",
            })
    return blocking, warning, info



def staging_coverage(shots: list[dict]) -> dict | None:
    """开场站位覆盖率。**只在表里真有这一列时才算。**

    为什么不做成阻断：站位是"填之前先定死"的预置，不是判据。
    没填不代表戏错了，只代表"位置还没决定"——而位置没决定，
    跨镜就会出现上一镜她在左、下一镜她在右，观众说不出哪里不对，只觉得怪。
    所以这里只报数，不拦稿。
    """
    if not shots or not any("staging" in s for s in shots):
        return None
    hints = ("（待填）", "（待填：谁在左/右·朝向）", "待填", "—", "-", "")
    empty = [s for s in shots
             if (s.get("staging") or "").strip() in hints]
    return {"n": len(shots), "empty": len(empty),
            "filled": len(shots) - len(empty),
            "shots": [s["no"] for s in empty]}


def analyze(shots: list[dict], fmt: str = "single", text: str = "",
            const: dict | None = None, mode: str = "ai",
            form: str | None = None, platform: str | None = None,
            model: str | None = None, is_ai: bool = True) -> dict:
    if not shots:
        raise ValueError("未解析到任何镜头")

    c = const or CONST
    d = c["drama"]
    if "form" not in d:
        d = apply_form_profile({"drama": d}, form or "live_action", mode)["drama"]
    dshot, dsingle, dq, dpanel, dasset = resolve_profile(d)
    prof = d.get("_profile", "ai")
    frm = d.get("form", "live_action")
    n = len(shots)
    total = sum(s["sec"] for s in shots)
    sec_dist = Counter(s["sec"] for s in shots)
    size_dist = Counter(s["size"] for s in shots)
    move_dist = Counter(s["move"] for s in shots)
    dlg = [s for s in shots if has_line(s)]
    fixed = [s for s in shots if s["move"] == "固定"]
    core = [s for s in shots if dshot["core_sec_min"] <= s["sec"] <= dshot["core_sec_max"]]
    long_take = [s for s in shots if s["sec"] >= dshot["long_take_sec"]]
    # ★ 近中景/特写景别必须按形态选名词：漫画形态用"大格/中格/特写格"，
    #   视频形态用"近景/中景/特写"。混用会让 cm_ratio 恒为 0、
    #   让特写反应镜判据整体失效（旧版实测 bug）。
    csizes = near_medium_set(d)          # 近中景：管"脸看得清"
    rxsizes = reaction_set(d)            # 反应镜：管"情绪格多长"，与上者是两个集合
    # 末镜有专属规则（single 要余味长镜 ≥long_take），与"反应镜不能太长"天然冲突。
    # 两条判据各行其道：反应镜判据排除末镜，末镜由结尾规则管。
    close_noline = [s for s in shots if s["size"] in rxsizes and not has_line(s)
                    and s["no"] != shots[-1]["no"]]

    fixed_ratio = len(fixed) / n
    cm_ratio = sum(size_dist.get(k, 0) for k in csizes) / n
    dlg_ratio = len(dlg) / n
    core_ratio = len(core) / n

    blocking, warning = [], []
    cliff_info = None

    # 黄金窗口：镜 1 不得超长，且不得用旁白交代
    first = shots[0]
    if first["sec"] > dsingle["hook_window_s"]:
        blocking.append({"check": "黄金窗口超长",
                         "value": f"镜 1 = {first['sec']}s", "limit": f"≤ {dsingle['hook_window_s']}s"})
    if "旁白" in (first.get("line") or ""):
        blocking.append({"check": "黄金窗口用旁白交代",
                         "value": first["line"][:24], "limit": "用画面/物件钉处境"})

    # 长镜必须承载内容（台词 / 字幕 / 旁白），不得是纯空镜
    # ★ 排除末镜：末镜是余味镜，按定义就是"不解释、不总结，只留一个动作
    #   或一个物件的变化"——**没有台词是它的正常形态**。跟反应镜判据
    #   排除末镜是同一个道理：末镜由结尾规则（余味镜 ≥long_take_sec）
    #   单独管，不能再叠一条"必须有台词"。
    last_no = shots[-1]["no"]
    bad_long = [s for s in long_take if not has_content(s) and s["no"] != last_no]
    if bad_long:
        blocking.append({"check": "长镜空镜",
                         "value": "镜 " + "/".join(str(s["no"]) for s in bad_long),
                         "limit": f"≥{dshot['long_take_sec']}s 必须含台词/字幕"})

    # 时长控制
    if fmt == "single":
        target = dsingle["target_duration_s"]
        tol = dsingle["duration_tolerance"]
        if abs(total - target) > target * tol:
            warning.append({"check": "单集时长偏离",
                            "value": f"{total:.0f}s", "limit": f"{target}±{tol * 100:.0f}%"})
        if not (dsingle["shots_min"] <= n <= dsingle["shots_max"]):
            warning.append({"check": "镜数越界", "value": str(n),
                            "limit": f"{dsingle['shots_min']}-{dsingle['shots_max']}"})

    # 结尾：single 要求余味长镜，series 要求钩子
    last = shots[-1]
    if fmt == "single":
        if last["sec"] < dshot["long_take_sec"]:
            warning.append({"check": "结尾镜偏短", "value": f"{last['sec']}s",
                            "limit": f"≥ {dshot['long_take_sec']}s（余味镜）"})
    else:
        if not has_ending_hook(last):
            blocking.append({"check": "连载结尾无钩子", "value": f"镜 {last['no']}",
                             "limit": "最后一镜必须留悬念"})
        # ★ 连载才查卡点评分：单集完结没有"付不付费"这回事。
        #   [cliff] 这张表此前整张空转（项目里 grep 不到 cliff），
        #   现在接上：结尾镜的卡点强度必须够。
        import cliff as CF
        cres = CF.score_ending(shots, d, rxsizes)
        cliff_info = cres
        if not cres["ok"]:
            warning.append({
                "check": "卡点评分不足",
                "value": f"{cres['score']:.0f} 分（{cres['detail']}）",
                "limit": f"付费集结尾卡点 ≥ {cres['min']} 分",
            })

    # 镜头语言
    if fixed_ratio < dq["fixed_ratio_min"]:
        warning.append({"check": "固定镜头占比偏低", "value": f"{fixed_ratio * 100:.0f}%",
                        "limit": f"≥ {dq['fixed_ratio_min'] * 100:.0f}%"})
    if cm_ratio < dq["close_medium_ratio_min"]:
        warning.append({"check": "近中景占比偏低", "value": f"{cm_ratio * 100:.0f}%",
                        "limit": f"≥ {dq['close_medium_ratio_min'] * 100:.0f}%"
                                 f"（{'、'.join(csizes)}）"})
    if core_ratio < dshot["core_ratio_min"]:
        # 区间写进检查名：AI 档是 3-4 秒，真人档是 5-10 秒，写死会误导
        span = f"{dshot['core_sec_min']:g}-{dshot['core_sec_max']:g}s"
        warning.append({"check": f"核心镜长({span})占比偏低", "value": f"{core_ratio * 100:.0f}%",
                        "limit": f"≥ {dshot['core_ratio_min'] * 100:.0f}%"})
    if not (dq["dialogue_ratio_min"] <= dlg_ratio <= dq["dialogue_ratio_max"]):
        warning.append({"check": "对白镜占比偏离", "value": f"{dlg_ratio * 100:.0f}%",
                        "limit": f"{dq['dialogue_ratio_min'] * 100:.0f}%-{dq['dialogue_ratio_max'] * 100:.0f}%"})
    over_reaction = [s for s in close_noline if s["sec"] > dshot["reaction_max_sec"]]
    if over_reaction:
        warning.append({"check": "特写反应镜偏长",
                        "value": "镜 " + "/".join(str(s["no"]) for s in over_reaction),
                        "limit": f"特写无台词镜 ≤ {dshot['reaction_max_sec']:g}s"})

    # 钩子密度（景别按形态取，否则漫剧全判断档）
    wins = hook_windows(shots, dq["hook_per_seconds"], rxsizes,
                         dshot["long_take_sec"])
    miss = [w for w in wins if not w["hook"]]
    if miss:
        spans = "、".join(f"{w['t0']:.0f}-{w['t1']:.0f}s" for w in miss)
        warning.append({"check": "钩子断档", "value": spans,
                        "limit": f"每 {dq['hook_per_seconds']}s 至少一个钩子"})

    # 秒级锚点：均匀窗口管"不许有长段平淡"，锚点管"该炸的秒数必须炸"。
    # 两者不互相替代：断档是兜底，锚点未命中的片子可能没有任何断档，
    # 但该爆破的 60s / 90s 上是平的——而平淡的爆破点就是划走点。
    anchor_miss, anchor_detail = hook_anchor_checks(shots, d, rxsizes)
    if anchor_miss:
        spans = "、".join(f"{a:g}s" for a in anchor_miss)
        warning.append({"check": "锚点未命中", "value": spans,
                        "limit": f"锚点 {fmt_anchors(hook_anchors(d))} 上"
                                 f"{(d.get('hook') or {}).get('anchor_gap_s', 15)}s 内必须有钩子"
                                 f"（0s = 对抗开场，无容差）"})

    # 分幕核对（表内标注 vs 逐镜相加）
    act_check = []
    for a in parse_acts(text):
        seg = [s for s in shots if a["lo"] <= s["no"] <= a["hi"]]
        actual = sum(s["sec"] for s in seg)
        act_check.append({**a, "actual": actual, "delta": actual - a["declared"],
                          "shots": len(seg)})
    act_drift = [a for a in act_check if a["delta"] != 0]

    # 形态专属判据（视频形态返回空三件套）
    cblock, cwarn, cinfo = comic_panel_checks(shots, d)
    blocking += cblock
    warning += cwarn

    # 景别白名单：视频形态的景别是有限枚举，写错模型抽错提示词。
    # 漫剧的白名单在 comic_panel_checks 里查（它用 [panel].sizes），
    # 这里只管视频形态——两处各用各的表，不共用一套名词。
    if not is_comic(d):
        allowed = sizes_for({"drama": d}, d.get("form"))
        bad_size = [s for s in shots if s["size"] and s["size"] not in allowed]
        if bad_size:
            blocking.append({
                "check": "景别不在白名单",
                "value": "镜 " + "/".join(f"{s['no']}({s['size']})" for s in bad_size[:8]),
                "limit": "、".join(allowed),
            })

    # 单镜太短：sec_min 定了却没人查，等于空转。
    # 0.5 秒的镜在成片里是一帧闪——观众看不清，也接不上戏。
    sec_min = float(dshot.get("sec_min") or 0)
    if sec_min > 0:
        too_short = [s for s in shots if 0 < s["sec"] < sec_min]
        if too_short:
            warning.append({
                "check": "单镜过短",
                "value": "镜 " + "/".join(f"{s['no']}({s['sec']:g}s)" for s in too_short[:8]),
                "limit": f"单镜 ≥ {sec_min:g}s（太短看不清，也接不上）",
            })

    # 合规（AIGC 标识 / 未成年人 / 违规敏感）
    kblock, kwarn, kinfo = compliance_checks(text, shots, d, frm, is_ai=is_ai)
    blocking += kblock
    warning += kwarn

    # 模型能力：超限即阻断，不许截断后调上游。
    # ★ 只对 AI 生成档查——真人实拍没有"生成后端"，镜长由表演决定，
    #   拿模型上限去拦真人长镜是把尺子用反了（实测：《撑伞》真人档
    #   10 秒长镜被 AI 后端的 8s 上限误拦）。
    if is_ai:
        mblock, mwarn, minfo = model_checks(shots, d, model)
    else:
        mblock, mwarn, minfo = [], [], {"skipped": "真人实拍，不计模型上限"}
    blocking += mblock
    warning += mwarn

    # 平台规格：时长档
    pblock, pwarn, pinfo = platform_checks(text, total, fmt, d, platform)
    warning += pwarn

    return {
        "n": n, "total": total, "avg": round(total / n, 2),
        "median": sorted(s["sec"] for s in shots)[n // 2],
        "form": frm,
        "sec_dist": dict(sorted(sec_dist.items())),
        "size_dist": dict(size_dist.most_common()),
        "move_dist": dict(move_dist.most_common()),
        "dialogue_n": len(dlg), "fixed_n": len(fixed),
        "fixed_ratio": round(fixed_ratio, 3), "cm_ratio": round(cm_ratio, 3),
        "dlg_ratio": round(dlg_ratio, 3), "core_ratio": round(core_ratio, 3),
        "close_sizes": list(csizes),
        "windows": wins, "hook_miss": len(miss),
        "act_check": act_check, "act_drift": act_drift,
        "staging": staging_coverage(shots),
        "cliff": cliff_info,
        "comic": cinfo, "compliance": kinfo, "model": minfo, "platform": pinfo,
        "blocking": blocking, "warning": warning,
        "baseline": {
            "source": "项目覆盖" if (c or {}).get("_has_overrides") else "全局默认",
            "profile": prof,
            "form": frm,
            "platform": pinfo.get("platform_aspect", ""),
            "target_s": dsingle["target_duration_s"] if fmt == "single"
                      else d["series"]["episode_target_s"],
            "shots_min": dsingle["shots_min"] if fmt == "single" else None,
            "shots_max": dsingle["shots_max"] if fmt == "single" else None,
        },
        "verdict": ("需修" if blocking else ("偏弱" if len(warning) >= 3 else ("良" if warning else "优"))),
    }



def analyze_changed(shots: list[dict], changed: list[int], fmt: str = "single",
                    text: str = "", const: dict | None = None,
                    full: dict | None = None, mode: str = "ai",
                    form: str | None = None, model: str | None = None,
                    is_ai: bool = True) -> dict:
    """增量重扫：只重检受影响镜头（修订后只重检受影响镜头）。

    为什么需要它：改 3 个镜头就全表重跑，代价随集数线性增长，而**只有改动点可能引入新问题**。
    但要诚实地区分两类判据——

    - **镜头局部判据**（可增量）：长镜空镜 / 特写反应镜偏长 / 黄金窗口（仅当镜 1 被改）
      / 结尾镜（仅当最后一镜被改）。这些只取决于被改的那几个镜头本身。
    - **全表比例判据**（必须全量）：固定镜占比 / 近中景占比 / 核心镜长占比 / 对白镜占比 /
      钩子断档 / 时长与镜数。这些是**分母相关的统计量**，改一个镜头会牵动全表。

    所以增量结果里，`local` 是本次重检的结论，`global_stale` 明确列出"因改动而失效、
    需全量复算"的比例判据名——不假装它们还能用。

    ★ `global_stale` 必须**按当前形态与档位动态生成**：写死成"核心镜长(3-4s)占比"
    在真人档（5-10s）下名字是错的，漫剧形态下"近景+中景占比"这个词根本不适用。
    """
    const = const or CONST
    d = const["drama"]
    if "form" not in d:
        d = apply_form_profile({"drama": d}, form or "live_action", mode)["drama"]
    dshot, dsingle, dq, _dpanel, _dasset = resolve_profile(d)
    csizes = near_medium_set(d)
    rxsizes = reaction_set(d)
    changed_set = set(changed)
    by_no = {s["no"]: s for s in shots}
    n = len(shots)

    local_blocking, local_warning, touched = [], [], []

    # 1) 被改镜头本身：长镜空镜（镜头局部）
    #    ★ 末镜豁免，理由同全量路径：末镜是余味镜，没台词是它的正常形态。
    last_no = shots[-1]["no"] if shots else None
    for no in sorted(changed_set):
        s = by_no.get(no)
        if not s:
            continue
        touched.append(no)
        if (s["sec"] >= dshot["long_take_sec"] and not has_content(s)
                and no != last_no):
            local_blocking.append({"check": "长镜空镜", "value": f"镜 {no}",
                                   "limit": f"≥{dshot['long_take_sec']}s 必须含台词/字幕"})
        # 2) 特写无台词反应镜偏长（镜头局部）
        if s["size"] in rxsizes and not has_line(s) and s["sec"] > dshot["reaction_max_sec"]:
            local_warning.append({"check": "特写反应镜偏长", "value": f"镜 {no}",
                                  "limit": f"特写无台词镜 ≤ {dshot['reaction_max_sec']:g}s"})

    # 3) 黄金窗口：只有当镜 1 本身被改时才重检
    if 1 in changed_set and shots:
        first = shots[0]
        if first["sec"] > dsingle["hook_window_s"]:
            local_blocking.append({"check": "黄金窗口超长", "value": f"镜 1 = {first['sec']}s",
                                   "limit": f"≤ {dsingle['hook_window_s']}s"})
        if "旁白" in (first.get("line") or ""):
            local_blocking.append({"check": "黄金窗口用旁白交代",
                                   "value": first["line"][:24], "limit": "用画面/物件钉处境"})

    # 4) 结尾判据：只有当最后一镜被改时才重检
    if shots and shots[-1]["no"] in changed_set:
        last = shots[-1]
        if fmt == "single":
            if last["sec"] < dshot["long_take_sec"]:
                local_warning.append({"check": "结尾镜偏短", "value": f"{last['sec']}s",
                                      "limit": f"≥ {dshot['long_take_sec']}s（余味镜）"})
        else:
            if not has_ending_hook(last):
                local_blocking.append({"check": "连载结尾无钩子", "value": f"镜 {last['no']}",
                                       "limit": "最后一镜必须留悬念"})

    # 4.5) 模型超限与合规：增量路径必须接上这两组，否则全量能拦的问题
    #      在 --changed 下被静默放过（"修订后只重检受影响镜头"≠"不检了"）。
    #      - 模型超限只取决于被改镜头自身（时长/参考图/提示词），是镜头局部判据；
    #      - 合规只扫正文，正文与分镜全量传入，结论不依赖 changed。
    changed_shots = [by_no[no] for no in sorted(changed_set) if no in by_no]
    if is_ai and changed_shots:
        mblock, mwarn, _ = model_checks(changed_shots, d, model)
        local_blocking += mblock
        local_warning += mwarn
    kblock, kwarn, _ = compliance_checks(text, shots, d,
                                         d.get("form") or form or "live_action",
                                         is_ai=is_ai)
    local_blocking += kblock
    local_warning += kwarn

    # 4.6) 漫画形态专属判据：气泡超长 / 景别运镜白名单 / 静态漫出现运镜。
    #      ★ 这几条**只取决于被改的那一镜**，是镜头局部判据，必须接上——
    #      实测：改一格的气泡对白到 80 字，全量报"气泡对白超长"（阻断），
    #      增量路径却一声不吭。漏接的后果比"不检"更坏——用户以为增量扫过了。
    if changed_shots:
        cblock, cwarn, _ = comic_panel_checks(changed_shots, d)
        local_blocking += cblock
        local_warning += cwarn

    # 5) 失效的全表比例判据：按当前形态与档位取真名，改动越多失效面越大
    span = f"{dshot['core_sec_min']:g}-{dshot['core_sec_max']:g}s"
    global_checks = ["固定镜头占比", f"近中景({'/'.join(csizes)})占比",
                     f"核心镜长({span})占比", "对白镜占比",
                     "钩子断档", "单集时长", "镜数越界",
                     # 同为全表判据：漏列等于告诉用户"这些没变的还能信"
                     "锚点未命中", "分幕核对", "平台时长档"]
    stale = list(global_checks) if changed_set else []


    # 用最近一次全量结果做对照（若提供），标出本次改动是否引入了新的局部问题
    prev_local = set()
    if full and "local_signature" in full:
        prev_local = set(full["local_signature"])
    now_sig = {f"{i['check']}@{i['value']}" for i in local_blocking + local_warning}
    new_issues = now_sig - prev_local

    return {
        "mode": "incremental", "changed": sorted(changed_set), "touched": touched,
        "n": n,
        "local": {"blocking": local_blocking, "warning": local_warning},
        "local_signature": sorted(now_sig),
        "new_issues": sorted(new_issues),
        "global_stale": stale,
        "note": (f"已重检 {len(touched)} 个改动镜头的局部判据；"
                 f"{len(stale)} 项全表比例判据因改动而失效，需全量复算"
                 if stale else "无改动，无需重扫"),
    }


def render_changed(res: dict, title: str = "") -> str:
    out = [C.bold(f"\n  短剧分镜 · 增量重扫 · {title or '(未命名)'}"), "  " + "─" * 56]
    ch = res["changed"]
    out.append(f"  改动镜头：{'、'.join('镜 ' + str(x) for x in ch) or '（无）'}"
               f"   全表 {res['n']} 镜")
    out.append("")
    lb, lw = res["local"]["blocking"], res["local"]["warning"]
    if not lb and not lw:
        out.append(C.ok("  ✓ 改动镜头的局部判据全部通过"))
    for label, items, color in (("阻断级", lb, C.err), ("警告级", lw, C.warn)):
        for i in items:
            mark = "← 新引入" if f"{i['check']}@{i['value']}" in set(res["new_issues"]) else ""
            out.append(color(f"  · [{label}] {i['check']}: {i['value']}  限 {i['limit']}  {mark}"))
            if i.get("fix"):
                out.append(C.dim(f"      ↳ 失败模式「{i.get('failure', '?')}」 {i['fix']}"))
    if res["global_stale"]:
        out.append("")
        out.append(C.warn(f"  ! 以下全表判据因改动失效，须全量复算（{len(res['global_stale'])} 项）："))
        out.append(C.dim("    " + "、".join(res["global_stale"])))
        out.append(C.dim("    → 跑不带 --changed 的完整扫描"))
    out.append("")
    return "\n".join(out)


def render(res: dict, title: str = "", const: dict | None = None) -> str:
    b = res.get("baseline") or {}
    dd = (const or CONST)["drama"]
    if "form" not in dd:
        dd = apply_form_profile({"drama": dd}, b.get("form", "live_action"),
                                b.get("profile", "ai"))["drama"]
    dshot, _ds, dq, _dp, _da = resolve_profile(dd)
    csizes = res.get("close_sizes") or list(near_medium_set(dd))
    n, total = res["n"], res["total"]
    FORM_NAME = {"live_action": "真人/AI 写实", "anime": "动漫短剧",
                 "motion_comic": "动态漫", "static_comic": "静态漫"}
    out = [C.bold(f"\n  短剧分镜扫描 · {title or '(未命名)'}"), "  " + "─" * 56]
    out.append(f"  {n} 镜   {total:.0f}s（{int(total // 60)}分{int(total % 60)}秒）"
               f"   均值 {res['avg']}s   中位 {res['median']}s")
    if b:
        rng = f" / {b['shots_min']}-{b['shots_max']} 镜" if b.get("shots_min") else ""
        tag = C.ok("项目覆盖") if b["source"] == "项目覆盖" else C.dim("全局默认")
        prof = b.get("profile", "ai")
        frm = b.get("form", "live_action")
        ptag = C.ok("真人实拍档") if prof == "live" else C.dim("AI生成档")
        ftag = C.cyan(FORM_NAME.get(frm, frm))
        asp = b.get("platform", "")
        out.append(C.dim(f"  基线  {tag}  {ftag}  {ptag}  目标 {b['target_s']}s{rng}"
                         + (f"  {asp}" if asp else "")))
    out.append("")

    out.append("  时长分布")
    mx = max(res["sec_dist"].values())
    for sec, cnt in res["sec_dist"].items():
        out.append(f"    {sec:>3.0f}s  {cnt:>2} 镜  {bar(cnt, mx, 14)}")
    out.append("  景别")
    for k, v in res["size_dist"].items():
        out.append(f"    {k:<5} {v:>2} 镜  {v / n * 100:5.1f}%")
    out.append("  运镜")
    for k, v in res["move_dist"].items():
        out.append(f"    {k:<5} {v:>2} 镜  {v / n * 100:5.1f}%")
    out.append(f"  对白镜 {res['dialogue_n']}/{n} = {res['dlg_ratio'] * 100:.1f}%"
               f"   固定镜 {res['fixed_ratio'] * 100:.0f}%"
               f"   近中景 {'/'.join(csizes)} {res['cm_ratio'] * 100:.0f}%"
               f"   核心镜长 {res['core_ratio'] * 100:.0f}%")

    ok_w = len(res["windows"]) - res["hook_miss"]
    win_total = len(res["windows"])
    hook_color = C.ok if res["hook_miss"] == 0 else C.warn
    out.append("  钩子   " + hook_color(f"{ok_w}/{win_total} 窗口达标")
               + f"（每 {dq['hook_per_seconds']}s 一个）")

    cinfo = res.get("comic") or {}
    stg = res.get("staging")
    cres = res.get("cliff")
    if cres:
        cc = C.ok if cres.get("ok") else C.warn
        cscore = "%.0f 分" % cres["score"]
        out.append("  卡点   " + cc(cscore)
                   + C.dim("   " + cres["detail"]))
    if stg and stg.get("empty"):
        out.append("  站位   " + C.warn(f"{stg['filled']}/{stg['n']} 已填")
                   + C.dim(f"   未填镜 {'/'.join(str(x) for x in stg['shots'][:8])}"
                           f"{'…' if len(stg['shots']) > 8 else ''}"
                           "（分镜前先定死左右与朝向）"))
    if cinfo.get("grids"):
        out.append(f"  格     {cinfo['grids']} 格  "
                   f"格组 {cinfo['grid_range'][0]}-{cinfo['grid_range'][1]}  "
                   f"气泡上限 {cinfo.get('bubble_cap')} 字"
                   + (f"  阅读顺序 {cinfo['reading_order']}" if cinfo.get("reading_order") else ""))
    minfo = res.get("model") or {}
    if minfo.get("max_shot_sec"):
        out.append(C.dim(f"  后端模型单镜上限 {minfo['max_shot_sec']}s"))

    if res["act_check"]:
        out.append("")
        out.append("  分幕核对（表内标注 vs 逐镜相加）")
        for a in res["act_check"]:
            flag = "" if a["delta"] == 0 else C.err(f"  ← 差 {a['delta']:+.0f}s")
            out.append(f"    {a['name']:<12} 镜 {a['lo']:>2}-{a['hi']:<2} {a['shots']:>2} 镜"
                       f"  标注 {a['declared']:>3}s  实际 {a['actual']:>3}s{flag}")
        if res["act_drift"]:
            out.append(C.warn("    · 表内时长与逐镜相加不符：手工核时不可靠，应以引擎计算为准"))

    for label, items, color in (("阻断级问题", res["blocking"], C.err),
                                ("警告级问题", res["warning"], C.warn)):
        if items:
            out.append("")
            out.append(color(f"  ■ {label} ({len(items)})"))
            for i in items:
                out.append(color(f"    · {i['check']}: {i['value']}  限 {i['limit']}"))
                # 失败模式分类（[failure.modes]）：不只说"超了"，还说清怎么修
                if i.get("fix"):
                    tag = f"失败模式「{i.get('failure', '?')}」"
                    out.append(C.dim(f"      ↳ {tag} {i['fix']}"))

    if not res["blocking"] and not res["warning"]:
        out.append(C.ok("\n  全部指标通过。"))
    else:
        v = res["verdict"]
        out.append("")
        out.append(f"  节拍健康  {C.ok(v) if v in ('优', '良') else C.err(v)}")
    out.append("")
    return "\n".join(out)



def _opts() -> tuple[list[str], dict]:
    """解析 argv → (位置参数, 选项字典)。带值的选项不会被误当成位置参数。"""
    valued = {"--format", "--changed", "--project", "--mode",
              "--form", "--platform", "--model"}
    pos, opt, i = [], {}, 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in valued:
            # 带值选项：即使后面是另一个 --flag 或已到末尾，也不能把它吞进选项值
            if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("--"):
                opt[a] = sys.argv[i + 1]
                i += 2
                continue
            opt[a] = ""
            i += 1
            continue
        if a.startswith("--"):
            opt[a] = True
            i += 1
            continue
        pos.append(a)
        i += 1
    return pos, opt


def main():
    pos, opt = _opts()
    if not pos:
        print("用法: python scan.py <分镜表.md|.csv> [--json] [--format single|series] "
              "[--mode ai|live] [--form live_action|anime|motion_comic|static_comic] "
              "[--platform 名] [--model 名] [--changed N,M] [--project <项目目录>]")
        return 1
    path = Path(pos[0])
    if not path.exists():
        print(C.err(f"文件不存在: {path}"))
        return 1
    fmt = opt.get("--format", "single")

    # 项目级阈值覆盖：显式 --project 优先，否则从分镜表所在目录向上自动查找。
    # 没有这一步，1 分钟短剧会被 140 秒的全局基线误报"时长偏离 / 镜数越界"。
    proj_root = Path(opt["--project"]) if opt.get("--project") else find_project_root(path)
    const = constants_for(proj_root, form=opt.get("--form")) if proj_root \
        else constants_for(None, form=opt.get("--form"))
    # 档位：--mode 显式指定 > 项目/全局 [drama] shot_profile > ai
    mode = str(opt.get("--mode") or const["drama"].get("shot_profile", "ai"))
    # ★ is_ai 必须跟着**解析出来的档位**走，不能只看 --live-shoot 开关。
    #   踩过的坑：项目 overrides 里写了 shot_profile = "live"，档位判据全切到
    #   真人长镜，模型上限却仍按 AI 档查——9 秒长镜被"后端 8s 上限"拦下。
    #   两档判据方向相反，尺子用反比不用尺子更糟。
    is_ai = False if opt.get("--live-shoot") else (mode != "live")

    text = path.read_text(encoding="utf-8")
    shots = parse_shotlist(text)
    if not shots:
        print(C.err("未解析到镜头，检查表格格式（md 主表 7 列 / csv 8 列）"))
        return 1

    # 增量重扫：--changed N,M / --changed N-M
    if opt.get("--changed"):
        changed = []
        for part in re.split(r"[,，]", str(opt["--changed"])):
            part = part.strip()
            if not part:
                continue
            m = re.match(r"^(\d+)\s*[-~]\s*(\d+)$", part)
            if m:
                changed.extend(range(int(m.group(1)), int(m.group(2)) + 1))
            elif part.isdigit():
                changed.append(int(part))
        res = analyze_changed(shots, changed, fmt=fmt, text=text, const=const,
                              mode=mode, is_ai=is_ai)
        print(json.dumps(res, ensure_ascii=False, indent=2) if opt.get("--json")
              else render_changed(res, path.parent.name))
        return 0

    res = analyze(shots, fmt=fmt, text=text, const=const, mode=mode,
                  platform=opt.get("--platform"), model=opt.get("--model"),
                  is_ai=is_ai)
    print(json.dumps(res, ensure_ascii=False, indent=2) if opt.get("--json")
          else render(res, path.parent.name, const=const))
    return 0 if not res["blocking"] else 1



if __name__ == "__main__":
    sys.exit(main())
