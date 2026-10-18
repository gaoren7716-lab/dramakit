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

from common import CONST, C, apply_shot_profile, bar, constants_for

D_ = CONST["drama"]
DS_ = D_["single"]
DSER_ = D_["series"]
DSHOT_ = D_["shot"]
DQ_ = D_["quality"]

# 台词里的钩子信号：疑问 / 否定 / 冲突
HOOK_LINE = ("？", "?", "凭什么", "为什么", "难道", "竟然", "居然", "没想到",
             "其实", "真相", "秘密", "骗", "撒谎", "闭嘴", "滚", "你敢",
             "分手", "离婚", "后悔")
# 画面里的事件信号：突发 / 动作转折 / 关键道具
HOOK_VISUAL = ("震动", "来电", "消息", "响起", "铃声", "突然", "瞬间", "推门",
               "起身", "站起", "跪", "耳光", "摔", "撕", "转身走", "拉住",
               "抱住", "吻", "拥抱", "亮起")
# 情绪推进型运镜才算钩子信号；缓拉/跟移/跟摇是收尾语言，不算
HOOK_MOVES = ("缓推", "推", "仰拍", "俯拍", "摇")

CLOSE_SIZES = ("特写", "大特写")

PROFILES = ("ai", "live")


def resolve_profile(d: dict, mode: str = "ai") -> tuple:
    """从 drama 表里取三张判据表 → (shot, single, quality)。

    `[drama.shot]` / `[drama.single]` / `[drama.quality]` 是 **AI 生成档**的基线；
    `[drama.live.*]` 只写与 AI 档的差异项，按表名分组逐表覆盖。

    档位应用本身发生在 `common.apply_shot_profile`——那里保证了
    「项目覆盖 > 档位 > 全局基线」的优先级。本函数只负责读取：

    - `d` 带 `_profile` 标记 → 上游已算好，直接用；此时**忽略 mode 参数**，
      因为带标记的常数已经合过项目覆盖，优先级高于命令行档位。
    - `d` 没有标记（调用方直接传了 CONST）→ 按 mode 现场应用一次。
    """
    if "_profile" not in d:
        d = apply_shot_profile({"drama": d}, mode)["drama"]
    return dict(d["shot"]), dict(d["single"]), dict(d["quality"])



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


def parse_shotlist(text: str) -> list[dict]:
    """解析主分镜表：md 表格（7 列）或 csv（8 列，末列英文提示词）。"""
    stripped = text.lstrip("\ufeff")
    shots = []

    if not any(l.strip().startswith("|") for l in stripped.splitlines()):
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

    for line in stripped.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells[0].isdigit():
            continue
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
                "visual": f"{cells[3]}｜{cells[4]}", "line": cells[5], "sound": "",
                "prompt_en": "",
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
    """该镜是否有台词。'无' 与 '—（字幕/画面文字）' 都不算。"""
    t = (s.get("line") or "").strip()
    if not t or t == "无":
        return False
    if t.startswith("—") or t.startswith("-"):
        return False
    return True


def has_content(s: dict) -> bool:
    """该镜是否承载内容（台词 / 字幕 / 旁白 / 画面文字）。长镜不得是纯空镜。"""
    t = (s.get("line") or "").strip()
    return bool(t) and t != "无"


def is_hook(s: dict) -> bool:
    """启发式钩子信号：情绪运镜 / 情绪特写 / 冲突台词 / 突发事件 / 信息密集长镜。"""
    if any(m in (s["move"] or "") for m in HOOK_MOVES):
        return True
    if s["size"] in CLOSE_SIZES:
        return True
    if any(w in (s.get("line") or "") for w in HOOK_LINE):
        return True
    if any(w in (s.get("visual") or "") for w in HOOK_VISUAL):
        return True
    if s["sec"] >= DSHOT_["long_take_sec"]:
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


def hook_windows(shots: list[dict], window_s: int) -> list[dict]:
    """按累计时长切窗口，返回每个窗口及其是否含钩子。"""
    wins, cur, t0, acc = [], [], 0.0, 0.0
    for s in shots:
        cur.append(s)
        acc += s["sec"]
        if acc >= window_s:
            wins.append({"t0": round(t0, 1), "t1": round(t0 + acc, 1),
                         "shots": cur, "hook": any(is_hook(x) for x in cur)})
            t0 += acc
            acc, cur = 0.0, []
    if cur:
        wins.append({"t0": round(t0, 1), "t1": round(t0 + acc, 1),
                     "shots": cur, "hook": any(is_hook(x) for x in cur)})
    return wins


def analyze(shots: list[dict], fmt: str = "single", text: str = "",
            const: dict | None = None, mode: str = "ai") -> dict:
    if not shots:
        raise ValueError("未解析到任何镜头")

    c = const or CONST
    d = c["drama"]
    dshot, dsingle, dq = resolve_profile(d, mode)
    prof = d.get("_profile", "ai")
    n = len(shots)
    total = sum(s["sec"] for s in shots)
    sec_dist = Counter(s["sec"] for s in shots)
    size_dist = Counter(s["size"] for s in shots)
    move_dist = Counter(s["move"] for s in shots)
    dlg = [s for s in shots if has_line(s)]
    fixed = [s for s in shots if s["move"] == "固定"]
    core = [s for s in shots if dshot["core_sec_min"] <= s["sec"] <= dshot["core_sec_max"]]
    long_take = [s for s in shots if s["sec"] >= dshot["long_take_sec"]]
    close_noline = [s for s in shots if s["size"] in CLOSE_SIZES and not has_line(s)]

    fixed_ratio = len(fixed) / n
    cm_ratio = (size_dist.get("近景", 0) + size_dist.get("中景", 0)) / n
    dlg_ratio = len(dlg) / n
    core_ratio = len(core) / n

    blocking, warning = [], []

    # 黄金 3 秒：镜 1 不得超长，且不得用旁白交代
    first = shots[0]
    if first["sec"] > dsingle["hook_window_s"]:
        blocking.append({"check": "黄金3秒超长",
                         "value": f"镜 1 = {first['sec']}s", "limit": f"≤ {dsingle['hook_window_s']}s"})
    if "旁白" in (first.get("line") or ""):
        blocking.append({"check": "黄金3秒用旁白交代",
                         "value": first["line"][:24], "limit": "用画面/物件钉处境"})

    # 长镜必须承载内容（台词 / 字幕 / 旁白），不得是纯空镜
    bad_long = [s for s in long_take if not has_content(s)]
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

    # 镜头语言
    if fixed_ratio < dq["fixed_ratio_min"]:
        warning.append({"check": "固定镜头占比偏低", "value": f"{fixed_ratio * 100:.0f}%",
                        "limit": f"≥ {dq['fixed_ratio_min'] * 100:.0f}%"})
    if cm_ratio < dq["close_medium_ratio_min"]:
        warning.append({"check": "近景+中景占比偏低", "value": f"{cm_ratio * 100:.0f}%",
                        "limit": f"≥ {dq['close_medium_ratio_min'] * 100:.0f}%"})
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

    # 钩子密度
    wins = hook_windows(shots, dq["hook_per_seconds"])
    miss = [w for w in wins if not w["hook"]]
    if miss:
        spans = "、".join(f"{w['t0']:.0f}-{w['t1']:.0f}s" for w in miss)
        warning.append({"check": "钩子断档", "value": spans,
                        "limit": f"每 {dq['hook_per_seconds']}s 至少一个钩子"})

    # 分幕核对（表内标注 vs 逐镜相加）
    act_check = []
    for a in parse_acts(text):
        seg = [s for s in shots if a["lo"] <= s["no"] <= a["hi"]]
        actual = sum(s["sec"] for s in seg)
        act_check.append({**a, "actual": actual, "delta": actual - a["declared"],
                          "shots": len(seg)})
    act_drift = [a for a in act_check if a["delta"] != 0]

    return {
        "n": n, "total": total, "avg": round(total / n, 2),
        "median": sorted(s["sec"] for s in shots)[n // 2],
        "sec_dist": dict(sorted(sec_dist.items())),
        "size_dist": dict(size_dist.most_common()),
        "move_dist": dict(move_dist.most_common()),
        "dialogue_n": len(dlg), "fixed_n": len(fixed),
        "fixed_ratio": round(fixed_ratio, 3), "cm_ratio": round(cm_ratio, 3),
        "dlg_ratio": round(dlg_ratio, 3), "core_ratio": round(core_ratio, 3),
        "windows": wins, "hook_miss": len(miss),
        "act_check": act_check, "act_drift": act_drift,
        "blocking": blocking, "warning": warning,
        "baseline": {
            "source": "项目覆盖" if (c or {}).get("_has_overrides") else "全局默认",
            "profile": prof,
            "target_s": dsingle["target_duration_s"] if fmt == "single"
                      else d["series"]["episode_target_s"],
            "shots_min": dsingle["shots_min"] if fmt == "single" else None,
            "shots_max": dsingle["shots_max"] if fmt == "single" else None,
        },
        "verdict": ("需修" if blocking else ("偏弱" if len(warning) >= 3 else ("良" if warning else "优"))),
    }


def analyze_changed(shots: list[dict], changed: list[int], fmt: str = "single",
                    text: str = "", const: dict | None = None,
                    full: dict | None = None, mode: str = "ai") -> dict:
    """增量重扫：只重检受影响镜头（修订后只重检受影响镜头）。

    为什么需要它：改 3 个镜头就全表重跑，代价随集数线性增长，而**只有改动点可能引入新问题**。
    但要诚实地区分两类判据——

    - **镜头局部判据**（可增量）：长镜空镜 / 特写反应镜偏长 / 黄金 3 秒（仅当镜 1 被改）
      / 结尾镜（仅当最后一镜被改）。这些只取决于被改的那几个镜头本身。
    - **全表比例判据**（必须全量）：固定镜占比 / 近中景占比 / 核心镜长占比 / 对白镜占比 /
      钩子断档 / 时长与镜数。这些是**分母相关的统计量**，改一个镜头会牵动全表。

    所以增量结果里，`local` 是本次重检的结论，`global_stale` 明确列出"因改动而失效、
    需全量复算"的比例判据名——不假装它们还能用。
    """
    const = const or CONST
    d = const["drama"]
    dshot, dsingle, dq = resolve_profile(d, mode)
    changed_set = set(changed)
    by_no = {s["no"]: s for s in shots}
    n = len(shots)

    local_blocking, local_warning, touched = [], [], []

    # 1) 被改镜头本身：长镜空镜（镜头局部）
    for no in sorted(changed_set):
        s = by_no.get(no)
        if not s:
            continue
        touched.append(no)
        if s["sec"] >= dshot["long_take_sec"] and not has_content(s):
            local_blocking.append({"check": "长镜空镜", "value": f"镜 {no}",
                                   "limit": f"≥{dshot['long_take_sec']}s 必须含台词/字幕"})
        # 2) 特写无台词反应镜偏长（镜头局部）
        if s["size"] in CLOSE_SIZES and not has_line(s) and s["sec"] > dshot["reaction_max_sec"]:
            local_warning.append({"check": "特写反应镜偏长", "value": f"镜 {no}",
                                  "limit": f"特写无台词镜 ≤ {dshot['reaction_max_sec']:g}s"})

    # 3) 黄金 3 秒：只有当镜 1 本身被改时才重检
    if 1 in changed_set and shots:
        first = shots[0]
        if first["sec"] > dsingle["hook_window_s"]:
            local_blocking.append({"check": "黄金3秒超长", "value": f"镜 1 = {first['sec']}s",
                                   "limit": f"≤ {dsingle['hook_window_s']}s"})
        if "旁白" in (first.get("line") or ""):
            local_blocking.append({"check": "黄金3秒用旁白交代",
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

    # 5) 失效的全表比例判据：改动越多，失效面越大
    global_checks = ["固定镜头占比", "近景+中景占比", "核心镜长(3-4s)占比", "对白镜占比",
                     "钩子断档", "单集时长", "镜数"]
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
    if res["global_stale"]:
        out.append("")
        out.append(C.warn(f"  ! 以下全表判据因改动失效，须全量复算（{len(res['global_stale'])} 项）："))
        out.append(C.dim("    " + "、".join(res["global_stale"])))
        out.append(C.dim("    → 跑不带 --changed 的完整扫描"))
    out.append("")
    return "\n".join(out)


def render(res: dict, title: str = "", const: dict | None = None) -> str:
    b = res.get("baseline") or {}
    _, _, dq = resolve_profile((const or CONST)["drama"], b.get("profile", "ai"))
    n, total = res["n"], res["total"]
    out = [C.bold(f"\n  短剧分镜扫描 · {title or '(未命名)'}"), "  " + "─" * 56]
    out.append(f"  {n} 镜   {total:.0f}s（{int(total // 60)}分{int(total % 60)}秒）"
               f"   均值 {res['avg']}s   中位 {res['median']}s")
    if b:
        rng = f" / {b['shots_min']}-{b['shots_max']} 镜" if b.get("shots_min") else ""
        tag = C.ok("项目覆盖") if b["source"] == "项目覆盖" else C.dim("全局默认")
        prof = b.get("profile", "ai")
        ptag = C.ok("真人实拍档") if prof == "live" else C.dim("AI生成档")
        out.append(C.dim(f"  基线  {tag}  {ptag}  目标 {b['target_s']}s{rng}"))
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
               f"   近中景 {res['cm_ratio'] * 100:.0f}%"
               f"   核心镜长 {res['core_ratio'] * 100:.0f}%")

    ok_w = len(res["windows"]) - res["hook_miss"]
    win_total = len(res["windows"])
    hook_color = C.ok if res["hook_miss"] == 0 else C.warn
    out.append("  钩子   " + hook_color(f"{ok_w}/{win_total} 窗口达标")
               + f"（每 {dq['hook_per_seconds']}s 一个）")

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
    valued = {"--format", "--changed", "--project", "--mode"}
    pos, opt, i = [], {}, 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a in valued and i + 1 < len(sys.argv):
            opt[a] = sys.argv[i + 1]
            i += 2
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
              "[--mode ai|live] [--changed N,M] [--project <项目目录>]")
        return 1
    path = Path(pos[0])
    if not path.exists():
        print(C.err(f"文件不存在: {path}"))
        return 1
    fmt = opt.get("--format", "single")

    # 项目级阈值覆盖：显式 --project 优先，否则从分镜表所在目录向上自动查找。
    # 没有这一步，1 分钟短剧会被 140 秒的全局基线误报"时长偏离 / 镜数越界"。
    proj_root = Path(opt["--project"]) if opt.get("--project") else find_project_root(path)
    const = constants_for(proj_root) if proj_root else CONST
    # 档位：--mode 显式指定 > 项目/全局 [drama] shot_profile > ai
    mode = str(opt.get("--mode") or const["drama"].get("shot_profile", "ai"))

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
        res = analyze_changed(shots, changed, fmt=fmt, text=text, const=const, mode=mode)
        print(json.dumps(res, ensure_ascii=False, indent=2) if opt.get("--json")
              else render_changed(res, path.parent.name))
        return 0

    res = analyze(shots, fmt=fmt, text=text, const=const, mode=mode)
    print(json.dumps(res, ensure_ascii=False, indent=2) if opt.get("--json")
          else render(res, path.parent.name, const=const))
    return 0


if __name__ == "__main__":
    sys.exit(main())
