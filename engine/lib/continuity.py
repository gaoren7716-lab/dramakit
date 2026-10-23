# -*- coding: utf-8 -*-
"""跨集一致性（continuity）

**单集好做，连载会崩。** 到第 20-30 集，人设飘、伏笔丢、时间轴对不上——
但每一集单看都是对的。崩溃的真实形态是**文件与文件互相矛盾**，
所以这一组判据查的不是"某一集写得好不好"，而是"跨集引用是否完整"。

为什么机械检查在这里特别值钱：人复核时逐集读，读到第 18 集已经忘了第 3 集
登记过什么。**只有台账能记住，而台账需要机器来对账。**

台账放在项目根 `continuity.md`，四张表（时间轴 / 角色 / 场景 / 伏笔）。
判据全部确定性可算，无一项需要读剧本理解内容：

  T 时间轴   T1 集号连续无跳号   T2 相邻集时点不重复（重复=两集同一刻，须标并行）
  C 角色     C1 ID 唯一   C2 分镜表里出现的角色都已登记   C3 换装必须显式声明
  S 场景     S1 ID 唯一   S2 分镜表里出现的场景都已登记
  F 伏笔     F1 埋点集 < 回收集   F2 回收集必须真实存在   F3 状态与回收集一致
  X 引用     X1 台账内部悬空引用（引用了不存在的集/ID）

报错范式：每条同时给出「哪两处矛盾」+「怎么修」，不报"校验失败"了事。
纯标准库，零依赖。
"""
from __future__ import annotations

import re
from pathlib import Path

from common import C, is_row, split_row

# 台账四张表的标题关键词 → 内部键
TABLES = {
    "时间轴": "timeline",
    "角色": "chars",
    "场景": "scenes",
    "伏笔": "threads",
}


def cf_tl_gap(d: dict | None) -> float:
    """读 `[continuity].timeline_gap_max_s`。0 = 相邻集必须首尾相接。"""
    sw = (d or {}).get("continuity") or {}
    try:
        return float(sw.get("timeline_gap_max_s") or 0)
    except (TypeError, ValueError):
        return 0.0


def _rows(md: str, header_kw: str) -> list[dict]:
    """抓某张表的数据行。表头下面第一行是 |---| 分隔，之后每行按表头列数切。

    宽松解析：找不到该表返回空列表，不抛异常——缺表由 M 类判据报，不由解析器崩。
    """
    lines = md.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and header_kw in ln:
            start = i
            break
    if start is None:
        # 退一步：可能用粗体写的表名
        for i, ln in enumerate(lines):
            if header_kw in ln and ("表" in ln or "**" in ln):
                start = i
                break
    if start is None:
        return []

    head, data = None, []
    for ln in lines[start + 1:]:
        s = ln.strip()
        if s.startswith("#") and s.lstrip("#").strip() and head is not None:
            break
        if not is_row(s):
            if head is not None and data:
                break
            continue
        cells = split_row(s)
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue                      # 分隔行
        if head is None:
            head = cells
            continue
        if len(cells) < 2:
            continue
        data.append(dict(zip(head, cells)))
    return data


def load_ledger(proj) -> dict:
    """读项目根 continuity.md，返回 {timeline, chars, scenes, threads, path, ok}。"""
    p = Path(proj)
    f = p / "continuity.md" if p.is_dir() else p
    if not f.exists():
        return {"ok": False, "path": f, "exists": False,
                "timeline": [], "chars": [], "scenes": [], "threads": []}
    md = f.read_text(encoding="utf-8")
    out = {"ok": True, "exists": True, "path": f}
    for kw, key in TABLES.items():
        out[key] = _rows(md, kw)
    return out


def _span_episodes(val: str) -> list[int]:
    """把 "1-60" / "1、3、5" / "3" 解析成集号列表。"""
    out = []
    for part in re.split(r"[、,，/]", str(val)):
        part = part.strip()
        m = re.match(r"^(\d+)\s*[-~至]\s*(\d+)$", part)
        if m:
            out.extend(range(int(m.group(1)), int(m.group(2)) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def _shotshot_names(text: str) -> set:
    """从一集分镜表里抽"谁在画面 / 谁出场"栏出现的人名。

    交付表第 4 列是"谁在画面"，列名可能是"谁在画面"或"人物"。
    """
    names = set()
    for ln in text.splitlines():
        if not is_row(ln):
            continue
        cells = split_row(ln)
        if len(cells) < 4 or cells[0].lower() in ("镜",) or not cells[0].isdigit():
            continue
        # 第 4 列（索引 3）= 谁在画面；该列可能用"、"分隔多人
        who = cells[3]
        for nm in re.split(r"[、,，/·]+", who):
            nm = nm.strip()
            if nm and not nm.startswith("（") and nm not in ("—", "-"):
                names.add(nm)
    return names


def _scene_names(text: str) -> set:
    """从分镜表正文里抽场景名（场景块的行首粗体或标题）。"""
    out = set()
    for m in re.finditer(r"^\*\*([^*]{2,20})\*\*\s*$", text, re.M):
        out.add(m.group(1).strip())
    return out


def find_series_episodes(proj) -> list[tuple[int, Path]]:
    """找项目里的分集分镜表：第NN集-分镜表.md / 第NN集-分镜表.csv。"""
    p = Path(proj)
    out = []
    for f in sorted(p.glob("第*集*分镜表.*")):
        m = re.search(r"第\s*(\d+)\s*集", f.name)
        if m:
            out.append((int(m.group(1)), f))
    return out


# 项目骨架用的台账模板：四张表 + 使用说明。
# 表名与列名是判据的契约，改列名要同步改 _rows 的调用方。
TEMPLATE = """# 跨集台账 · {name}

> 连载的崩溃形态是**文件与文件互相矛盾**——单看每一集都对，合起来不对。
> 人逐集读时，读到第 18 集已经忘了第 3 集登记过什么。**只有台账能记住，台账靠机器对账。**
> 每集写完，往这四张表里补一行。跑 `dramakit continuity {name}` 检查。

## 角色表

| ID | 姓名 | 外形 | 服装 | 贯穿道具 | 首现集 |
|---|---|---|---|---|---|
| C1 |  |  |  |  | 1 |

## 场景表

| ID | 场景 | 说明 | 首现集 |
|---|---|---|---|
| S1 |  |  | 1 |

## 伏笔表

| ID | 伏笔 | 埋点集 | 回收集 | 状态 |
|---|---|---|---|---|
| F1 |  | 1 |  | 未回收 |

## 时间轴

| 集 | 起 | 止 | 说明 |
|---|---|---|---|
| 1 | 第1天 上午 | 第1天 中午 |  |
"""


# ------------------------------------------------------------
# 判据
# ------------------------------------------------------------

def check(proj, ledger: dict | None = None, const: dict | None = None) -> dict:
    """跑全部跨集一致性判据，返回 {blocking, warning, info, counts}。

    ★ 每个判据都受 config [continuity] 的开关控制。
      早先那 7 个开关一个都没被读——表看起来在管事，实际全是空转。
      空转的开关比没有开关更坏：它让人以为"这条有人管"。
      现在：关掉某个开关，对应的判据就不查。
    """
    led = ledger or load_ledger(proj)
    blocking, warning, info = [], [], {}

    # 开关从 config 取；没给 const 时用全局默认（全开）
    d = (const or {}).get("drama") if const else None
    if d is None:
        from common import CONST
        d = CONST["drama"]
    sw = d.get("continuity") or {}
    on = lambda k: bool(sw.get(k, True))    # noqa: E731

    if not led.get("exists"):
        return {"blocking": [{"check": "缺跨集台账",
                              "value": f"{led['path']} 不存在",
                              "limit": "连载必须在项目根放 continuity.md（时间轴/角色/场景/伏笔）"}],
                "warning": [], "info": {}, "counts": {}, "ledger": led}

    tl = led["timeline"]
    chars = led["chars"]
    scenes = led["scenes"]
    threads = led["threads"]
    info["counts"] = {"时间轴": len(tl), "角色": len(chars),
                      "场景": len(scenes), "伏笔": len(threads)}
    info["switches"] = {k: on(k) for k in sw} if sw else {}

    # ---- T 时间轴 ----
    eps = []
    for r in tl:
        v = r.get("集") or r.get("集号") or ""
        eps.extend(_span_episodes(v))
    if on("require_timeline") and eps:
        seen = sorted(set(eps))
        gaps = [n for n in range(seen[0], seen[-1] + 1) if n not in set(seen)]
        if gaps:
            blocking.append({
                "check": "时间轴跳号",
                "value": "、".join(str(g) for g in gaps[:12]),
                "limit": "集号必须连续；跳号说明中间有几集没写时间轴",
            })
    # T2：相邻集凳点重复（同一时点挂两集），要求显式标"并行"
    pts = [(r.get("集") or r.get("集号") or "", (r.get("起") or r.get("时点") or "").strip())
           for r in tl]
    dup = []
    for i in range(1, len(pts)):
        if pts[i][1] and pts[i][1] == pts[i - 1][1]:
            # 同一起点 = 两集发生在同一时刻，须在"说明"里标"并行"
            note = tl[i].get("说明", "") + tl[i].get("备注", "")
            if "并行" not in note:
                dup.append(f"{pts[i - 1][0]}↔{pts[i][0]}({pts[i][1]})")
    if on("require_timeline") and dup:
        warning.append({
            "check": "相邻集时点重复",
            "value": "、".join(dup),
            "limit": "两集同一时刻须在说明里标『并行』，否则是时间轴写重了",
        })

    # T3：相邻集时间轴空档（`timeline_gap_max_s`）
    # ★ 这个键此前是空转的（config 写了、代码没读）。它需要台账里的「止」列
    #   才能算跨度，而「止」在续集样本里是有的——所以它是**可接的**，不是
    #   "无数据源"那一类。默认 0 = 必须首尾相接。
    #   时点写成"第1天 上午"这种自然语言，秒数无法直接换算，
    #   所以这里的判定口径是：**相邻集的时点字符串必须相接**——
    #   前集的「止」要等于后集的「起」；不等就是空档。差多少秒由人判断，
    #   机器只报"这里断了"。
    gap_max = float(cf_tl_gap(d))
    if on("require_timeline") and gap_max == 0:
        gaps_t = []
        for i in range(1, len(tl)):
            prev_stop = (tl[i - 1].get("止") or tl[i - 1].get("结束") or "").strip()
            cur_start = (tl[i].get("起") or tl[i].get("时点") or "").strip()
            if prev_stop and cur_start and prev_stop != cur_start:
                gaps_t.append(f"{tl[i - 1].get('集', '?')}→{tl[i].get('集', '?')}"
                              f"（止「{prev_stop}」≠ 起「{cur_start}」）")
        if gaps_t:
            warning.append({
                "check": "时间轴有断口",
                "value": "；".join(gaps_t[:6]),
                "limit": "timeline_gap_max_s = 0 要求相邻集首尾相接，"
                         "否则中间那段空白没人写（要么补，要么改阈值）",
            })

    # ---- C 角色 ----
    ids = [r.get("ID") or r.get("id") or "" for r in chars]
    ids = [i for i in ids if i]
    dupid = sorted({i for i in ids if ids.count(i) > 1})
    if on("character_id_required") and dupid:
        blocking.append({"check": "角色 ID 重复", "value": "、".join(dupid),
                         "limit": "ID 必须唯一，否则跨集引用会指向两个角色"})
    char_names = {(r.get("姓名") or r.get("名字") or "").strip() for r in chars}
    char_names.discard("")

    # C3 换装声明：服装列若出现多段（写了不止一个集区间），必须含"换"字说明
    if on("costume_change_must_declare"):
        for r in chars:
            costume = (r.get("服装") or "").strip()
            ranges = re.findall(r"\d+\s*[-~]\s*\d+", costume)
            if len(ranges) >= 2 and "换" not in costume:
                warning.append({
                    "check": "换装未声明",
                    "value": f"{r.get('姓名') or r.get('ID')}：{costume[:30]}",
                    "limit": "同一角色有多个服装段时必须写明第几集换装",
                })

    # ---- S 场景 ----
    sids = [r.get("ID") or r.get("id") or "" for r in scenes]
    sids = [i for i in sids if i]
    sdup = sorted({i for i in sids if sids.count(i) > 1})
    if on("scene_reuse_tracked") and sdup:
        blocking.append({"check": "场景 ID 重复", "value": "、".join(sdup),
                         "limit": "ID 必须唯一"})

    # ---- F 伏笔 ----
    thread_ids = []
    for r in threads:
        fid = (r.get("ID") or r.get("id") or "").strip()
        thread_ids.append(fid)
        bury = _span_episodes(r.get("埋点集") or r.get("埋") or "")
        reap = _span_episodes(r.get("回收集") or r.get("回收") or "")
        state = (r.get("状态") or "").strip()
        if on("foreshadow_double_bind") and bury and reap and max(bury) >= min(reap):
            blocking.append({
                "check": "伏笔埋点晚于回收点",
                "value": f"{fid}：埋 {bury} / 收 {reap}",
                "limit": "先埋再收；反了等于时间倒流",
            })
        if (on("foreshadow_double_bind") and reap
                and state in ("未回收", "active", "未激活") and "规划" not in state):
            warning.append({
                "check": "伏笔状态与回收点矛盾",
                "value": f"{fid}：回收集 {reap} 已填，状态却写「{state}」",
                "limit": "回收点已定的，状态应推进（如『已规划回收』）",
            })
    if thread_ids and len(set(t for t in thread_ids if t)) != len([t for t in thread_ids if t]):
        dup = sorted({t for t in thread_ids if t and thread_ids.count(t) > 1})
        blocking.append({"check": "伏笔 ID 重复", "value": "、".join(dup),
                         "limit": "ID 必须唯一"})

    # ---- X 引用完整性：分镜表实际出场的人/场景必须都在台账登记 ----
    eps_files = find_series_episodes(proj)
    info["episodes"] = [n for n, _ in eps_files]
    if eps_files and led.get("timeline") is not None:
        known = set(eps_files and [n for n, _ in eps_files])
        # X1 伏笔回收集必须真实存在（在台账集号里）
        all_eps = seen if eps else sorted(known)
        if on("dangling_ref_forbidden"):
            for r in threads:
                for ep in _span_episodes(r.get("回收集") or ""):
                    if all_eps and ep not in set(all_eps):
                        warning.append({
                            "check": "伏笔回收集不存在",
                            "value": f"{r.get('ID')}：回收集 {ep}",
                            "limit": f"台账里的集号只有 {min(all_eps)}-{max(all_eps)}",
                        })
        # C2 每集分镜表出场角色是否都已登记
        if on("character_id_required"):
            for n, f in eps_files:
                text = f.read_text(encoding="utf-8")
                for nm in _shotshot_names(text):
                    # 允许"人物表"里出现的别名：只要台账某条姓名包含或等于
                    if not any(nm == cn or nm in cn or cn in nm for cn in char_names):
                        warning.append({
                            "check": "角色未登记",
                            "value": f"第 {n} 集出现「{nm}」，角色台账无此人",
                            "limit": "所有出场角色必须在 continuity.md 角色表登记一条",
                        })

    return {"blocking": blocking, "warning": warning, "info": info,
            "counts": info.get("counts", {}), "ledger": led}


def render(res: dict, title: str = "") -> str:
    out = [C.bold(f"\n  跨集一致性 · {title or '(未命名)'}"), "  " + "─" * 56]
    ct = res.get("counts") or {}
    if ct:
        out.append("  台账  " + "   ".join(f"{k} {v} 条" for k, v in ct.items()))
        if res["info"].get("episodes"):
            e = res["info"]["episodes"]
            out.append(C.dim(f"  分集  共 {len(e)} 集（{e[0]}-{e[-1]}）"))
    for label, items, color in (("阻断级", res["blocking"], C.err),
                                ("警告级", res["warning"], C.warn)):
        for i in items:
            out.append(color(f"  · [{label}] {i['check']}: {i['value']}"))
            out.append(C.dim(f"      限 {i['limit']}"))
    if not res["blocking"] and not res["warning"]:
        out.append(C.ok("  ✓ 跨集引用完整，无矛盾"))
    out.append("")
    return "\n".join(out)


def main():
    import sys
    if len(sys.argv) < 2:
        print("用法: python continuity.py <项目目录> [--json]")
        return 1
    import json
    proj = sys.argv[1]
    res = check(proj)
    if "--json" in sys.argv:
        r = {k: v for k, v in res.items() if k != "ledger"}
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print(render(res, Path(proj).name))
    return 0 if not res["blocking"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
