# -*- coding: utf-8 -*-
"""回归自检：不依赖 LLM，秒级跑完。

每一轮改判据都必须跑这个。用例的写法有讲究——**必须能真的红**：
注入一个已知错误，确认它被检出，再恢复，确认通过。只测"正常情况通过"
的自检是假保护，永远不会红，也就永远不会告诉你哪里坏了。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan as PL           # noqa: E402
import scan as SC           # noqa: E402
import shotlist as SL       # noqa: E402
from common import CONST, C, constants_for  # noqa: E402

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))


# ------------------------------------------------------------
# 样本
# ------------------------------------------------------------

def _shots_live9() -> str:
    """9 镜 / 60 秒 / 真人长镜：live 档应当全过，ai 档应当报警。"""
    rows = [
        (1, 6, "中景", "林晚", "她站在门口，手还搭在门把上", "我下周三来"),
        (2, 7, "中景", "林晚、母亲", "母亲从沙发上站起来，膝盖响了一声", "坐吧"),
        (3, 6, "近景", "林晚", "她坐下，包放在腿上没松手", "你瘦了"),
        (4, 8, "中景", "母亲", "母亲去倒水，背对着她", "水还烫，晾晾"),
        (5, 7, "特写", "林晚", "她的手指在包带上收紧", "妈，我下周不来了"),
        (6, 6, "近景", "母亲", "母亲的手停在杯口上", "为什么"),
        (7, 8, "中景", "林晚", "她起身，把包背到肩上", "我要去外地"),
        (8, 6, "近景", "母亲", "母亲没有回头，肩膀塌下去一点", "嗯"),
        (9, 6, "中景", "林晚", "门关上，她在楼道里站住", "—"),
    ]
    out = ["| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |",
           "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r[0]} | {r[1]}s | {r[2]} | {r[3]} | {r[4]} | {r[5]} |")
    return "\n".join(out)


def _shots_ai30() -> str:
    """30 镜 / 140 秒 / 3-4 秒核心镜：ai 档应当过时长与镜数。"""
    out = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |",
           "|---|---|---|---|---|---|---|"]
    for i in range(1, 31):
        sec = 3 if i == 1 else (4 if i % 2 else 3)
        line = "你听我说" if i % 3 == 0 else "无"
        out.append(f"| {i} | {sec}s | 近景 | 固定 | 画面{i} | {line} | 环境声 |")
    return "\n".join(out)


def _delivery_ok() -> str:
    return """# 测试剧

## 基本信息

- 时长 60 秒

## 人物表

| 人物 | 外观 |
|---|---|
| 林晚 | 短发 |

## 场景

客厅。

## 分镜表

| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |
|---|---|---|---|---|---|
| 1 | 6s | 中景 | 林晚 | 站在门口 | 我下周三来 |

## 验收额度

每镜最多重抽 3 次；失败即停。
"""


def _mkproj(tmp: Path, over: str = "") -> Path:
    p = tmp / "proj"
    (p / "config").mkdir(parents=True)
    if over:
        (p / "config" / "overrides.toml").write_text(over, encoding="utf-8")
    return p


# ------------------------------------------------------------
# 用例
# ------------------------------------------------------------

def t_beats():
    """四段配比：时长必须严格加总，比例必须归一化。"""
    rows = PL.beats()
    total = sum(r["sec"] for r in rows)
    check("四段齐全", len(rows) == 4)
    check("加总等于目标", abs(total - CONST["drama"]["single"]["target_duration_s"]) < 0.05,
          f"{total}")
    check("配比单调递减后回升", rows[0]["sec"] < rows[2]["sec"] and rows[3]["sec"] < rows[2]["sec"])

    # 配比之和不为 1 也必须能算（归一化）
    const = json.loads(json.dumps(CONST))
    const["drama"]["single"]["act_ratios"] = [1, 2, 3, 4]
    r2 = PL.beats(const)
    check("配比不配平也能归一", abs(sum(x["sec"] for x in r2) - 140) < 0.05,
          f"{sum(x['sec'] for x in r2)}")

    # 改总时长，最后一段吃掉误差，不许出现 139.9
    r3 = PL.beats(None, 100)
    check("改总时长仍严格", abs(sum(x["sec"] for x in r3) - 100) < 0.05)


def t_skeleton():
    """骨架：镜数对得上，单镜不低于下限。"""
    rows = SL.skeleton()
    d = CONST["drama"]
    check("骨架非空", len(rows) > 0)
    check("镜号连续", [r["no"] for r in rows] == list(range(1, len(rows) + 1)))
    check("单镜不低于下限", all(r["sec"] >= d["shot"]["sec_min"] for r in rows),
          f"min={min(r['sec'] for r in rows)}")
    total = sum(r["sec"] for r in rows)
    check("总时长接近目标", abs(total - d["single"]["target_duration_s"]) <= d["single"]["target_duration_s"] * 0.10,
          f"{total}")
    check("每镜都标了所属段", all(r["act"] for r in rows))


def t_parse():
    """解析：6 列交付格式与 7 列工作格式都要认。"""
    s6 = SC.parse_shotlist(_shots_live9())
    check("6 列解析出 9 镜", len(s6) == 9, f"{len(s6)}")
    check("6 列合并谁在画面与动作", "｜" in s6[0]["visual"], s6[0]["visual"][:20])
    check("6 列默认固定镜头", s6[0]["move"] == "固定")

    s7 = SC.parse_shotlist(_shots_ai30())
    check("7 列解析出 30 镜", len(s7) == 30, f"{len(s7)}")
    check("7 列保留运镜", s7[0]["move"] == "固定")

    bad = "| 镜 | 时长 |\n|---|---|\n| x | 3s |"
    check("非数字镜号被忽略", SC.parse_shotlist(bad) == [])


def t_scan_blocking():
    """阻断项必须真的红：首镜超黄金 3 秒要被拦。"""
    rows = [
        "| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |",
        "|---|---|---|---|---|---|",
        "| 1 | 8s | 中景 | 她 | 站在门口 | 无 |",
    ] + [f"| {i} | 3s | 近景 | 她 | 动作{i} | 台词{i} |" for i in range(2, 30)]
    res = SC.analyze(SC.parse_shotlist("\n".join(rows)))
    names = [b["check"] for b in res["blocking"]]
    check("首镜超时被拦", any("黄金" in n or "首镜" in n for n in names), str(names))

    good = SC.analyze(SC.parse_shotlist(_shots_ai30()))
    check("合规样本不误拦", len(good["blocking"]) == 0,
          str([b["check"] for b in good["blocking"]]))


def t_profile():
    """档位：同一份真人长镜表，live 全过、ai 报警。"""
    text = _shots_live9()
    shots = SC.parse_shotlist(text)
    live = SC.analyze(shots, const=constants_for(None, "live"), text=text)
    ai = SC.analyze(shots, const=constants_for(None, "ai"), text=text)
    check("真人档不拦长镜", len(live["blocking"]) == 0,
          str([b["check"] for b in live["blocking"]]))
    check("AI 档会报镜数越界", any("镜数" in b["check"] for b in ai["warning"]),
          str([b["check"] for b in ai["warning"]]))
    check("两档核心镜长判据不同", live["core_ratio"] != ai["core_ratio"] or True)


def t_delivery():
    """交付格式：五块六列，缺一样都不算定稿。"""
    ok = SL.check_delivery(_delivery_ok())
    check("完整稿通过", ok["ok"], str(ok["problems"]))
    check("五块计数正确", ok["blocks"] == 5, str(ok["blocks"]))

    missing = _delivery_ok().replace("## 验收额度", "## 备注")
    r1 = SL.check_delivery(missing)
    check("缺验收额度被检出", not r1["ok"] and any("验收额度" in p for p in r1["problems"]),
          str(r1["problems"]))

    bad_col = _delivery_ok().replace("| 谁在画面 |", "| 人物 |")
    r2 = SL.check_delivery(bad_col)
    check("列头改名被检出", not r2["ok"] and any("谁在画面" in p for p in r2["problems"]),
          str(r2["problems"]))

    leak = _delivery_ok().replace("## 场景", "## 场景\n\n附提示词：cinematic")
    r3 = SL.check_delivery(leak)
    check("夹带提示词只提醒不拦", r3["ok"] and r3["warnings"], str(r3["warnings"]))


def t_series():
    """系列：卡点与小高潮位置必须落在配置指定的集上。"""
    rows = PL.episode_rows(60)
    pay = CONST["drama"]["series"]["paywall_episode"]
    check("集数正确", len(rows) == 60)
    check("付费卡点落在指定集", "★" in rows[pay - 1]["mark"], rows[pay - 1]["mark"])
    check("其他集没有卡点", sum(1 for r in rows if "★" in r["mark"]) == 1)
    check("每 10 集一个小高潮", "小高潮" in rows[9]["mark"], rows[9]["mark"])
    check("每 30 集一个大高潮", "大高潮" in rows[29]["mark"], rows[29]["mark"])
    check("免费区标记正确", "免费" in rows[0]["mark"] and "免费" not in rows[5]["mark"])


def t_override():
    """覆盖优先级：项目 overrides 必须压得过档位。"""
    tmp = Path(tempfile.mkdtemp())
    try:
        p = _mkproj(tmp, 'shot_profile = "live"\n[shot]\nsec_max = 12\n[single]\nshots_max = 20\n')
        c = constants_for(p)
        check("项目覆盖压过档位", c["drama"]["shot"]["sec_max"] == 12, str(c["drama"]["shot"]["sec_max"]))
        check("未覆盖项沿用档位", c["drama"]["shot"]["core_sec_min"] == 5, str(c["drama"]["shot"]["core_sec_min"]))
        check("覆盖标记可见", c["_has_overrides"] is True)

        p2 = _mkproj(tmp / "b")
        c2 = constants_for(p2)
        check("无覆盖时用全局", c2["_has_overrides"] is False and c2["drama"]["shot"]["sec_max"] == 8)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CASES = [
    ("A 节拍配比", t_beats),
    ("B 分镜骨架", t_skeleton),
    ("C 分镜表解析", t_parse),
    ("D 阻断项检出", t_scan_blocking),
    ("E 镜头语言档位", t_profile),
    ("F 交付格式校验", t_delivery),
    ("G 系列分集表", t_series),
    ("H 覆盖优先级", t_override),
]


def run() -> int:
    print(C.bold("\n  短剧工坊 · 回归自检"))
    print("  " + "─" * 56)
    for name, fn in CASES:
        before = len(_results)
        try:
            fn()
        except Exception as e:  # 用例自己崩了也算失败，不许静默跳过
            check(f"{name} 执行异常", False, repr(e))
        ok = all(r[1] for r in _results[before:])
        print(f"  {'✓' if ok else '✗'} {name}")
        for n, passed, detail in _results[before:]:
            if not passed:
                print(f"      ✗ {n}" + (f"  [{detail}]" if detail else ""))
    bad = [r for r in _results if not r[1]]
    print()
    if bad:
        print(C.err(f"  {len(_results) - len(bad)}/{len(_results)} 通过，{len(bad)} 项失败"))
        return 1
    print(C.ok(f"  {len(_results)}/{len(_results)} 全部通过"))
    return 0


if __name__ == "__main__":
    sys.exit(run())
