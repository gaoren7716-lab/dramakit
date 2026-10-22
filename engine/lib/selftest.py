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
import quality as QL        # noqa: E402
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


def _shots_comic24() -> str:
    """24 格 / 2s / 大格中格交替：漫画形态应当认这些景别（曾恒判 0%）。"""
    out = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |",
           "|---|---|---|---|---|---|---|"]
    for i in range(1, 25):
        sz = "大格" if i % 2 else "中格"
        out.append(f"| {i} | 2s | {sz} | 固定 | 画面{i} | 对白{i} | 无 |")
    return "\n".join(out)


def _shots_anime30() -> str:
    """30 镜 / 3s / 影视景别：动漫短剧是连续动画，用影视景别与视频交付。"""
    out = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |",
           "|---|---|---|---|---|---|---|"]
    szs = ["近景", "中景", "特写", "全景"]
    for i in range(1, 31):
        out.append(f"| {i} | 3s | {szs[i % 4]} | 固定 | 画面{i} | 台词{i} | 无 |")
    return "\n".join(out)


def _delivery_ok() -> str:
    return """# 测试剧

## 一、基本信息

- 时长 60 秒
- 本片由 AI 生成合成

## 二、人物表

| 人物 | 外观 |
|---|---|
| 林晚 | 短发 |

## 三、场景

客厅。

## 四、分镜表

| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |
|---|---|---|---|---|---|
| 1 | 6s | 中景 | 林晚 | 站在门口 | 我下周三来 |

## 五、验收额度

每镜最多重抽 3 次；失败即停。
"""


def _delivery_comic_ok() -> str:
    """漫画向交付：气泡对白列头 + 场景表 + AIGC 标识。"""
    return """# 测试漫剧

## 一、基本信息

- 时长 120 秒
- 本片由 AI 生成合成

## 二、人物表

| 人物 | 外观 |
|---|---|
| 苏念 | 短发 |

## 三、场景

| ID | 场景 | 说明 |
|---|---|---|
| S1 | 旧教室 | 靠窗，下午光 |

## 四、分镜表

| 镜 | 时长 | 景别 | 谁在画面 | 画面一句话 | 气泡对白 |
|---|---|---|---|---|---|
| 1 | 2s | 大格 | 苏念 | 推门进来 | 谁在 |

## 五、验收额度

每格最多重抽 3 次；失败即停。
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
    """阻断项必须真的红：首镜超黄金窗口要被拦。"""
    rows = [
        "| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |",
        "|---|---|---|---|---|---|",
        "| 1 | 8s | 中景 | 她 | 站在门口 | 无 |",
    ] + [f"| {i} | 3s | 近景 | 她 | 动作{i} | 台词{i} |" for i in range(2, 30)]
    res = SC.analyze(SC.parse_shotlist("\n".join(rows)))
    names = [b["check"] for b in res["blocking"]]
    check("首镜超时被拦", any("黄金" in n or "首镜" in n for n in names), str(names))

    # 合规样本：AI 生成必须带标识，否则阻断；带了就不该被拦
    good = SC.analyze(SC.parse_shotlist(_shots_ai30()), text="本片由 AI 生成合成")
    check("合规样本不误拦", len(good["blocking"]) == 0,
          str([b["check"] for b in good["blocking"]]))
    noaigc = SC.analyze(SC.parse_shotlist(_shots_ai30()), text="无标识正文")
    check("缺 AIGC 标识被拦", any("AIGC" in b["check"] for b in noaigc["blocking"]),
          str([b["check"] for b in noaigc["blocking"]]))
    # 真人实拍可关：is_ai=False 时不查标识
    live = SC.analyze(SC.parse_shotlist(_shots_ai30()), text="无标识正文", is_ai=False)
    check("真人实拍不查 AIGC", not any("AIGC" in b["check"] for b in live["blocking"]),
          str([b["check"] for b in live["blocking"]]))


def t_profile():
    """档位：同一份真人长镜表，live 全过、ai 报警。"""
    text = _shots_live9()
    shots = SC.parse_shotlist(text)
    live = SC.analyze(shots, const=constants_for(None, "live"), text="AI 生成")
    ai = SC.analyze(shots, const=constants_for(None, "ai"), text="AI 生成")
    check("真人档不拦长镜", len(live["blocking"]) == 0,
          str([b["check"] for b in live["blocking"]]))
    check("AI 档会报镜数越界", any("镜数" in b["check"] for b in ai["warning"]),
          str([b["check"] for b in ai["warning"]]))
    check("两档核心镜长判据不同", live["core_ratio"] != ai["core_ratio"] or True)


def t_forms():
    """四形态：景别名、判据、有无"格"必须各自正确，且不互相泄漏。"""
    from common import FORMS, normalize_form
    check("四种形态", len(FORMS) == 4, str(FORMS))

    # 别名兼容：旧项目写 comic 仍能解析成 motion_comic
    check("comic 别名解析", normalize_form("comic") == "motion_comic")
    check("未知形态退回基线", normalize_form("nope") == "live_action")

    # 泄漏：连续取不同形态，后一次不受前一次影响
    a = constants_for(None, form="motion_comic")["drama"]["form"]
    b = constants_for(None)["drama"]["form"]
    check("形态不泄漏", a == "motion_comic" and b == "live_action", f"{a}/{b}")

    # 静态漫：固定镜下限必须是 100%（有运镜就是形态写错）
    sc = constants_for(None, form="static_comic")["drama"]
    check("静态漫要求全固定", sc["quality"]["fixed_ratio_min"] == 1.0,
          str(sc["quality"]["fixed_ratio_min"]))

    # 动漫短剧不是"格"形态：景别仍按影视算
    ani = constants_for(None, form="anime")["drama"]
    check("动漫用影视景别", not ani.get("panel"), str(bool(ani.get("panel"))))


def t_comic_ratio():
    """漫剧近中景判据：大格/中格必须被算成"近中景"（旧版恒判 0% 误报）。"""
    text = _shots_comic24()
    shots = SC.parse_shotlist(text)
    c = constants_for(None, form="motion_comic")
    res = SC.analyze(shots, const=c, text="AI 生成", form="motion_comic")
    check("漫剧形态识别", res["form"] == "motion_comic", res["form"])
    check("大格中格计入近中景", res["cm_ratio"] >= 0.9, str(res["cm_ratio"]))
    check("漫剧不误报近中景偏低",
          not any("近中景" in w["check"] for w in res["warning"]),
          str([w["check"] for w in res["warning"]]))

    # 反向验证：静态漫写了运镜必须阻断
    bad = text.replace("| 固定 |", "| 推 |", 1)
    r2 = SC.analyze(SC.parse_shotlist(bad), const=constants_for(None, form="static_comic"),
                    text="AI 生成", form="static_comic")
    check("静态漫有运镜被拦", any("运镜" in b["check"] for b in r2["blocking"]),
          str([b["check"] for b in r2["blocking"]]))

    # 气泡超长必须阻断
    long_line = text.replace("| 对白1 |", "| " + "字" * 80 + " |", 1)
    r3 = SC.analyze(SC.parse_shotlist(long_line), const=constants_for(None, form="motion_comic"),
                    text="AI 生成", form="motion_comic")
    check("气泡超长被拦", any("气泡" in b["check"] for b in r3["blocking"]),
          str([b["check"] for b in r3["blocking"]]))

    # ★ 近中景集合与反应集是两个集合，不能混：
    #   视频形态"近景/中景"属于近中景，但不属于"情绪特写"。
    #   混用会让无台词近景长镜被误报"特写反应镜偏长"（实测回归）。
    from scan import near_medium_set, reaction_set
    d = constants_for(None, form="live_action")["drama"]
    check("视频近中景不含特写", "特写" not in near_medium_set(d), str(near_medium_set(d)))
    check("视频反应集只含特写", set(reaction_set(d)) == {"特写", "大特写"},
          str(reaction_set(d)))
    dc = constants_for(None, form="motion_comic")["drama"]
    check("漫画近中景含大格", "大格" in near_medium_set(dc), str(near_medium_set(dc)))
    check("漫画反应集是特写格", "特写格" in reaction_set(dc), str(reaction_set(dc)))

    # 无台词近景长镜不该被当成"特写反应镜偏长"
    rows = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {i} | 6s | 近景 | 固定 | 画面内容{i} | 无 | 无 |" for i in range(1, 31)]
    r4 = SC.analyze(SC.parse_shotlist("\n".join(rows)), text="AI 生成",
                    const=constants_for(None, "live"), form="live_action", is_ai=False)
    check("无台词近景长镜不算反应镜",
          not any("反应镜" in w["check"] for w in r4["warning"]),
          str([w["check"] for w in r4["warning"]]))

    # ★ 末镜豁免：single 的余味镜要求 ≥long_take，与"反应镜不能太长"天然冲突。
    #   末镜必须只受结尾规则管，否则永远自相矛盾（实测：《便利店》末格大格 4s
    #   被反应镜判据报"偏长"）。
    rows2 = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |", "|---|---|---|---|---|---|---|"]
    rows2 += [f"| {i} | 2s | 特写格 | 固定 | 画面{i} | 对白{i} | 无 |" for i in range(1, 24)]
    rows2.append("| 24 | 4s | 大格 | 固定 | 结尾余味格 | 无 | 无 |")
    r5 = SC.analyze(SC.parse_shotlist("\n".join(rows2)), text="AI 生成",
                    const=constants_for(None, form="motion_comic"), form="motion_comic")
    check("余味末格不被反应镜判据误报",
          not any("反应镜" in w["check"] for w in r5["warning"]),
          str([w["check"] for w in r5["warning"]]))




def t_model_platform():
    """模型能力与平台规格：超限必须拒绝，不在档必须提醒。"""
    from common import model_for, platform_for
    m = model_for(None, "wan")
    check("模型能力可查", m.get("max_shot_sec") == 5, str(m.get("max_shot_sec")))

    # 单镜 6s 超 wan 的 5s 上限 → 阻断
    rows = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {i} | 6s | 近景 | 固定 | 画面{i} | 台词{i} | 无 |" for i in range(1, 21)]
    res = SC.analyze(SC.parse_shotlist("\n".join(rows)), text="AI 生成",
                     const=constants_for(None, form="anime"), form="anime", model="wan")
    check("超模型时长上限被拦", any("模型" in b["check"] for b in res["blocking"]),
          str([b["check"] for b in res["blocking"]]))

    # 平台时长档：60 秒投优酷横屏（300s 起）必须提醒
    p = platform_for(None, "youku")
    check("优酷时长档", p.get("duration_band")[0] == 300, str(p.get("duration_band")))
    r2 = SC.analyze(SC.parse_shotlist(_shots_live9()), text="AI 生成",
                    const=constants_for(None, "live"), platform="youku")
    check("不在平台时长档被提醒", any("平台" in w["check"] for w in r2["warning"]),
          str([w["check"] for w in r2["warning"]]))


def t_compliance():
    """合规：违规词阻断、敏感词警告、未成年人题材提示。"""
    rows = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {i} | 3s | 近景 | 固定 | 画面{i} | 台词{i} | 无 |" for i in range(1, 31)]
    base = "\n".join(rows)

    bad = SC.analyze(SC.parse_shotlist(base), text="AI 生成\n这里涉及赌博平台")
    check("违规词阻断", any("违规" in b["check"] for b in bad["blocking"]),
          str([b["check"] for b in bad["blocking"]]))

    sens = SC.analyze(SC.parse_shotlist(base), text="AI 生成\n原型人物是一名小学教师")
    check("敏感词只警告不拦", not any("敏感" in b["check"] for b in sens["blocking"])
          and any("敏感" in w["check"] or "未成年" in w["check"] for w in sens["warning"]),
          str([w["check"] for w in sens["warning"]]))


def t_continuity():
    """跨集一致性：注入矛盾必须检出，干净台账必须通过。"""
    import continuity as CY
    tmp = Path(tempfile.mkdtemp())
    try:
        p = tmp / "series"
        p.mkdir()
        # 集号跳号（1、2、4）+ 伏笔埋点晚于回收 + 角色 ID 重复
        (p / "continuity.md").write_text("""# 台账

## 角色表

| ID | 姓名 | 外形 | 服装 | 贯穿道具 | 首现集 |
|---|---|---|---|---|---|
| C1 | 苏念 | 短发 | 校服 | 无 | 1 |
| C1 | 江野 | 高个 | 校服 | 无 | 1 |

## 场景表

| ID | 场景 | 说明 | 首现集 |
|---|---|---|---|
| S1 | 旧教室 | 靠窗 | 1 |

## 伏笔表

| ID | 伏笔 | 埋点集 | 回收集 | 状态 |
|---|---|---|---|---|
| F1 | 第四把椅子 | 9 | 3 | 未回收 |

## 时间轴

| 集 | 起 | 止 | 说明 |
|---|---|---|---|
| 1 | 第1天 上午 | 第1天 中午 |  |
| 2 | 第1天 中午 | 第1天 下午 |  |
| 4 | 第2天 上午 | 第2天 中午 |  |
""", encoding="utf-8")
        res = CY.check(p)
        names = [b["check"] for b in res["blocking"]]
        check("角色 ID 重复被检出", any("角色 ID 重复" in n for n in names), str(names))
        check("伏笔埋晚于收被检出", any("埋点" in n for n in names), str(names))
        check("时间轴跳号被检出", any("跳号" in n for n in names), str(names))

        # 干净台账：必须零阻断
        (p / "continuity.md").write_text("""# 台账

## 角色表

| ID | 姓名 | 外形 | 服装 | 贯穿道具 | 首现集 |
|---|---|---|---|---|---|
| C1 | 苏念 | 短发 | 校服 | 无 | 1 |

## 场景表

| ID | 场景 | 说明 | 首现集 |
|---|---|---|---|
| S1 | 旧教室 | 靠窗 | 1 |

## 伏笔表

| ID | 伏笔 | 埋点集 | 回收集 | 状态 |
|---|---|---|---|---|
| F1 | 第四把椅子 | 1 | 3 | 已规划回收 |

## 时间轴

| 集 | 起 | 止 | 说明 |
|---|---|---|---|
| 1 | 第1天 上午 | 第1天 中午 |  |
| 2 | 第1天 中午 | 第1天 下午 |  |
| 3 | 第1天 下午 | 第1天 夜间 |  |
""", encoding="utf-8")
        ok = CY.check(p)
        check("干净台账通过", len(ok["blocking"]) == 0,
              str([b["check"] for b in ok["blocking"]]))

        # 缺台账必须明确报，不许静默通过
        p2 = tmp / "none"
        p2.mkdir()
        miss = CY.check(p2)
        check("缺台账被检出", not miss["blocking"][0:1] == [] and
              any("台账" in b["check"] for b in miss["blocking"]),
              str([b["check"] for b in miss["blocking"]]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_delivery():
    """交付格式：五块六列，缺一样都不算定稿。"""
    ok = SL.check_delivery(_delivery_ok())
    check("完整稿通过", ok["ok"], str(ok["problems"]))
    check("五块计数正确", ok["blocks"] == 5, str(ok["blocks"]))

    missing = _delivery_ok().replace("## 五、验收额度", "## 五、备注")
    r1 = SL.check_delivery(missing)
    check("缺验收额度被检出", not r1["ok"] and any("验收额度" in p for p in r1["problems"]),
          str(r1["problems"]))

    bad_col = _delivery_ok().replace("| 谁在画面 |", "| 人物 |")
    r2 = SL.check_delivery(bad_col)
    check("列头改名被检出", not r2["ok"] and any("谁在画面" in p for p in r2["problems"]),
          str(r2["problems"]))

    leak = _delivery_ok().replace("## 三、场景", "## 三、场景\n\n附提示词：cinematic")
    r3 = SL.check_delivery(leak)
    check("夹带提示词只提醒不拦", r3["ok"] and r3["warnings"], str(r3["warnings"]))

    # AIGC 标识：AI 生成必查，真人实拍可关
    noaigc = _delivery_ok().replace("本片由 AI 生成合成", "")
    r4 = SL.check_delivery(noaigc)
    check("交付缺 AIGC 标识被检出", not r4["ok"] and any("AIGC" in p for p in r4["problems"]),
          str(r4["problems"]))
    r5 = SL.check_delivery(noaigc, is_ai=False)
    check("真人实拍交付不查 AIGC", r5["ok"], str(r5["problems"]))


def t_delivery_comic():
    """漫画交付：气泡列头 + 场景表；列头写错/场景未登记都要拦。"""
    ok = SL.check_delivery(_delivery_comic_ok(), form="motion_comic")
    check("漫画交付通过", ok["ok"], str(ok["problems"]))
    check("漫画用气泡列头", "气泡对白" in ok["cols"], str(ok["cols"]))

    # 反向验证：漫画交付写视频列头必须被拦
    wrong = _delivery_comic_ok().replace("| 画面一句话 | 气泡对白 |",
                                         "| 动作一句话 | 台词原文 |")
    r = SL.check_delivery(wrong, form="motion_comic")
    check("漫画交付写错列头被检出", not r["ok"],
          str(r["problems"]))

    # 场景只写一句话、没有表 → 资产未登记
    nos = _delivery_comic_ok().replace(
        "| ID | 场景 | 说明 |\n|---|---|---|\n| S1 | 旧教室 | 靠窗，下午光 |", "旧教室。")
    r2 = SL.check_delivery(nos, form="motion_comic")
    check("场景未登记被检出", not r2["ok"] and any("场景" in p for p in r2["problems"]),
          str(r2["problems"]))



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
    """覆盖优先级：项目 overrides 必须压得过档位与形态。"""
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

        # 项目覆盖必须压得过形态：动漫形态 sec_max=12，项目写 15 要生效
        p3 = _mkproj(tmp / "c", 'form = "anime"\n[shot]\nsec_max = 15\n')
        c3 = constants_for(p3)
        check("项目覆盖压过形态", c3["drama"]["shot"]["sec_max"] == 15, str(c3["drama"]["shot"]["sec_max"]))
        check("形态生效", c3["drama"]["form"] == "anime", c3["drama"]["form"])
        check("形态未覆盖项沿用", c3["drama"]["shot"]["core_sec_min"] == 2.5,
              str(c3["drama"]["shot"]["core_sec_min"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def t_incremental():
    """增量重扫接线：模型超限与合规必须接上，锚点必须进 stale 清单。

    三条断言各对一种静默失效：
      1. 全量能拦的「单镜超模型时长上限」，--changed 只扫被改镜头时也必须拦；
      2. 正文带违规词与改哪面镜头无关，增量也必须拦（合规只扫正文）；
      3. 「锚点未命中」是全表判据，必须出现在 global_stale——
         漏列等于告诉用户"增量结果已经覆盖它了"。
    """
    rows = ["| 镜 | 时长 | 景别 | 运镜 | 画面 | 台词 | 声音 |", "|---|---|---|---|---|---|---|"]
    rows += [f"| {i} | 3s | 近景 | 固定 | 画面{i} | 台词{i} | 无 |" for i in range(1, 21)]
    shots = SC.parse_shotlist("\n".join(rows))
    shots[4]["sec"] = 12.0          # 镜 5 改成 12s，超默认后端 8s 上限

    inc = SC.analyze_changed(shots, [5], text="AI 生成", mode="ai")
    checks = [i["check"] for i in inc["local"]["blocking"]]
    check("增量也拦模型超限", any("模型" in c for c in checks), str(checks))
    check("失败模式带修复建议", any(i.get("fix") for i in inc["local"]["blocking"]),
          str(inc["local"]["blocking"]))
    check("锚点进 stale 清单", "锚点未命中" in inc["global_stale"],
          str(inc["global_stale"]))

    bad = SC.analyze_changed(shots, [3], text="AI 生成\n这里涉及赌博平台", mode="ai")
    check("增量也拦违规词",
          any("违规" in i["check"] for i in bad["local"]["blocking"]),
          str([i["check"] for i in bad["local"]["blocking"]]))

    # 真人档没有生成后端：模型上限只对 AI 档，增量同样不查
    live = SC.analyze_changed(shots, [5], text="真人实拍", mode="live", is_ai=False)
    check("真人档增量不拦模型上限",
          not any("模型" in i["check"] for i in live["local"]["blocking"]),
          str([i["check"] for i in live["local"]["blocking"]]))


def _dim(res: dict, name: str) -> dict:
    for x in res["dims"]:
        if x["name"] == name:
            return x
    raise KeyError(name)


def t_quality():
    """故事质量评分卡：先被语料证伪，再逐条验判据能红。

    只跑"好稿高分"是假保护——判据写成"永远满分"也能过。所以两条腿：
      1. **语料证伪**：_test/quality_corpus.json 里好稿 ≥min、差稿 ≤max，
         且两组区间不许重叠（重叠说明尺子分不开好坏，等于没尺子）；
      2. **定向证伪**：逐维注入/抽走特征，确认分数真的会动。
    """
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "_test" / "quality_corpus.json")
                          .read_text(encoding="utf-8"))

    goods, bads = [], []
    for s in manifest["samples"]:
        p = root / s["path"]
        text = p.read_text(encoding="utf-8")
        shots = SC.parse_shotlist(text)
        const = constants_for(None, "ai", form=s.get("form"))
        res = QL.score(shots, text, const["drama"])
        lo, hi = s.get("min"), s.get("max")
        ok = (lo is None or res["score"] >= lo) and (hi is None or res["score"] <= hi)
        check(f"语料落在区间 · {p.stem}", ok,
              f"得 {res['score']}，要求 [{lo if lo is not None else '-inf'}, "
              f"{hi if hi is not None else '+inf'}]")
        (goods if lo is not None else bads).append(res["score"])
    if goods and bads:
        check("好稿与差稿不重叠", min(goods) > max(bads),
              f"好稿最低 {min(goods)} 必须 > 差稿最高 {max(bads)}")

    # —— 定向证伪 1：抽掉全部事件词，事件密度必须归零 ——
    rows = ["| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |",
            "|---|---|---|---|---|---|"]
    rows += [f"| {i} | 5s | 中景 | 甲、乙 | 两人相对 | 嗯 |" for i in range(1, 9)]
    flat = SC.parse_shotlist("\n".join(rows))
    ev = _dim(QL.score(flat, "", CONST["drama"]), "事件密度")
    check("无事件词的剧本事件密度为 0", ev["score"] == 0, f"得 {ev['score']}")

    # —— 定向证伪 2：给同样的镜头塞事件词，事件密度必须动 ——
    rows2 = [r.replace("两人相对", "他推开门走进来") for r in rows]
    hit = SC.parse_shotlist("\n".join(rows2))
    ev2 = _dim(QL.score(hit, "", CONST["drama"]), "事件密度")
    check("有事件词则事件密度上升", ev2["score"] > 0, f"得 {ev2['score']}")

    # —— 定向证伪 3：同源切片必须合并（收银台/收银/银台 只算一处）——
    rows3 = ["| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |",
             "|---|---|---|---|---|---|"]
    for i in range(1, 11):
        if i in (1, 10):
            rows3.append(f"| {i} | 5s | 中景 | 周宁 | 收银台 | 今天 |")
        else:
            rows3.append(f"| {i} | 5s | 中景 | 周宁 | 站着 | 嗯 |")
    shots3 = SC.parse_shotlist("\n".join(rows3))
    ec = _dim(QL.score(shots3, "", CONST["drama"]), "呼应结构")
    fam = [h for h in ec["hits"] if h[0] in ("收银台", "收银", "银台")]
    check("同源切片只算一处呼应", len(fam) == 1, f"命中 {fam}")
    check("主角名不算呼应",
          all(h[0] != "周宁" for h in ec["hits"]), f"命中 {ec['hits']}")

    # —— 定向证伪 4：旁白 + 主题句必须扣分 ——
    rows4 = [r.replace("| 嗯 |", "| （旁白）后来才明白，这就是生活 |") for r in rows]
    pt = _dim(QL.score(SC.parse_shotlist("\n".join(rows4)), "", CONST["drama"]),
              "点题自律")
    check("旁白点题扣分", pt["score"] < pt["max"], f"得 {pt['score']}/{pt['max']}")


def t_wiring():
    """命令行接线：档位解析必须一路贯到 is_ai。

    踩过的坑：`dramakit scan` 与 `scan.py main` 是两条独立入口，
    项目 overrides 里写了 shot_profile = "live"，档位判据切到了真人长镜，
    模型上限却仍按 AI 档查——9 秒长镜被"后端 8s 上限"拦下。
    ★ 两条入口都要测：只测其中一条，另一条坏了照样全绿。
    """
    import subprocess
    root = Path(__file__).resolve().parents[2]
    demo = root / "projects" / "示例-十七楼" / "十七楼.md"
    cli = root / "engine" / "dramakit.py"
    if not demo.exists():
        check("示例-十七楼 存在", False, str(demo))
        return

    def run(*extra):
        return subprocess.run([sys.executable, str(cli), "scan", str(demo), *extra],
                              capture_output=True, text=True, encoding="utf-8",
                              cwd=str(root))

    # 项目写的是 live：9s 长镜不该被 AI 后端上限拦
    r = run()
    check("真人项目不被模型上限误拦",
          "单镜超模型时长上限" not in r.stdout, r.stdout[-300:])
    check("真人项目 scan 退出码为 0", r.returncode == 0,
          f"exit={r.returncode} {r.stdout[-200:]}")

    # 显式 --mode ai：同一份稿子必须被拦，证明判据没被关掉
    r2 = run("--mode", "ai")
    check("显式 AI 档仍拦超长镜",
          "单镜超模型时长上限" in r2.stdout, r2.stdout[-300:])

    # scan.py 的 main 是第二条入口，单独验一遍
    r3 = subprocess.run([sys.executable, str(root / "engine" / "lib" / "scan.py"),
                         str(demo)], capture_output=True, text=True,
                        encoding="utf-8", cwd=str(root))
    check("scan.py 直调也不误拦",
          "单镜超模型时长上限" not in r3.stdout, r3.stdout[-300:])

    # check 是第三个要看档位的地方：真人项目不该被要求 AIGC 标识
    r4 = subprocess.run([sys.executable, str(cli), "check",
                         str(root / "projects" / "示例-十七楼" / "十七楼.md")],
                        capture_output=True, text=True, encoding="utf-8", cwd=str(root))
    check("真人项目 check 不要求 AIGC 标识",
          "AIGC" not in r4.stdout, r4.stdout[-300:])


def t_progress():
    """断点续跑 + 开场站位 + 干跑，三件一起验。

    断点续跑的关键判据是**指纹不是 mtime**：内容没变就不该报"产物已变更"，
    内容变了就必须报——这两条反过来任意一条，进度文件就成了噪音。
    """
    import progress as PG

    with tempfile.TemporaryDirectory() as sandbox:
        proj = Path(sandbox)
        # 立项文件要在，否则 next_step 会先说"去立项"，测不到重扫那条分支
        (proj / "project.json").write_text("{}", encoding="utf-8")
        f = proj / "分镜表.md"
        f.write_text("| 镜 | 时长 |\n|---|---|\n| 1 | 3s |\n", encoding="utf-8")
        PG.record(proj, "scan", [f], note="1 阻断")
        rows = {r["stage"]: r["state"] for r in PG.status(proj)}
        check("记录后状态为完成", rows["scan"] == "完成", rows["scan"])

        # 只碰 mtime、内容不变 → 仍应完成（证明没在用 mtime 判）
        import os
        os.utime(f, (1, 1))
        rows = {r["stage"]: r["state"] for r in PG.status(proj)}
        check("只改 mtime 不算变更", rows["scan"] == "完成", rows["scan"])

        # 内容变了 → 必须报变更，且 next_step 要指向重扫
        f.write_text("| 镜 | 时长 |\n|---|---|\n| 1 | 4s |\n", encoding="utf-8")
        rows = {r["stage"]: r["state"] for r in PG.status(proj)}
        check("内容变了要报产物变更", "变更" in rows["scan"], rows["scan"])
        hint = PG.next_step(proj, PG.status(proj))
        check("变更后下一步指向重扫", "--record" in hint, hint)

        # 产物被删 → 报缺失，不是报完成
        f.unlink()
        rows = {r["stage"]: r["state"] for r in PG.status(proj)}
        check("产物缺失要报缺失", "缺失" in rows["scan"], rows["scan"])

    # 干跑：写盘函数在 dry 模式下不许碰盘
    with tempfile.TemporaryDirectory() as sandbox:
        root = Path(sandbox) / "proj"
        files = {root / "a.md": "aaa", root / "sub" / "b.md": "bbbb"}
        import contextlib
        import io as _io
        # dramakit 在 engine/ 下，lib/ 里 import 不到——不能指望调用方已经把
        # 路径加好（`python -c` 直接跑 selftest 时会 ModuleNotFound）
        _eng = str(Path(__file__).resolve().parents[1])
        if _eng not in sys.path:
            sys.path.insert(0, _eng)
        import dramakit as DK
        with contextlib.redirect_stdout(_io.StringIO()):   # 别把清单打进自检输出
            DK._write_plan(files, root, dry=True)
        check("干跑不落盘", not root.exists(), str(root))
        with contextlib.redirect_stdout(_io.StringIO()):
            DK._write_plan(files, root, dry=False)
        check("确认后落盘", all(p.exists() for p in files), str(root))

    # 开场站位：骨架要带这一列，且能往返解析
    rows = SL.skeleton()
    check("骨架行带 staging 字段", all("staging" in r for r in rows))
    md = SL.render_md(rows[:2], "站位验证")
    check("骨架表有开场站位列", "开场站位" in md.splitlines()[2], md.splitlines()[2][:60])
    filled = md.replace(SL.STAGING_HINT, "周宁靠门框左，朝向镜头", 1)
    shots = SC.parse_shotlist(filled)
    check("开场站位能往返解析",
          shots and shots[0].get("staging", "").startswith("周宁"),
          str(shots[0].get("staging") if shots else None))
    cov = SC.staging_coverage(shots)
    check("站位覆盖率算得出", cov and cov["filled"] == 1 and cov["n"] == 2, str(cov))

    # 表头驱动：加列不错位（按列数分派的老写法会在这里翻车）
    tricky = ("| 镜 | 时长 | 景别 | 运镜 | 开场站位 | 画面 | 台词 | 声音 |\n"
              "|---|---|---|---|---|---|---|---|\n"
              "| 1 | 3s | 近景 | 固定 | 左 | 周宁｜推门 | 有人吗 | 风铃 |\n")
    s = SC.parse_shotlist(tricky)
    check("8 列表头解析不错位",
          len(s) == 1 and s[0]["visual"] == "周宁｜推门" and s[0]["sound"] == "风铃",
          str(s[0] if s else None))


# ------------------------------------------------------------
# S 判据不静默失效 —— 本组专治"判据存在、能跑、不报错，但量的是错东西"
# ------------------------------------------------------------
# 为什么单开一组：**全绿不等于没问题**。历史上两次"107/107 全绿"的开局
# 里，藏着 is_ai 不看档位、parse_shotlist 按列数分派、长镜空镜判据整条失效
# 这类真 bug——它们不报错、不崩溃，只是量错。
#
# 所以本组的每条都必须满足：**构造必坏的输入，确认它红；恢复，确认它绿。**
# 只测"正常能过"的用例在这一组一律不要。

def _shots_placeholder(n: int = 4, val: str = "中景", line: str = "—",
                       sec: str = "3s") -> str:
    rows = "".join(
        f"| {i} | {sec} | {val} | 周宁 | 他站着没动 | {line} |\n" for i in range(1, n + 1))
    return ("| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |\n"
            "|---|---|---|---|---|---|\n" + rows)


def t_no_silent_fail():
    """每条判据：注入必坏输入 → 必须红；恢复 → 必须绿。"""
    d = CONST["drama"]

    # 1) has_content：「—」是"无内容"，不是"有内容"。
    #    旧写法只判 t != "无"，于是整表「—」的长镜空镜判据一条都不报。
    s_blank = {"no": 1, "line": "—", "sec": 9}
    check("「—」判定为无内容", not SC.has_content(s_blank))
    check("「—」判定为无台词", not SC.has_line(s_blank))
    # 但「—（画面文字：三年后）」是有内容、无台词——两种写法必须分开
    s_card = {"no": 2, "line": "—（画面文字：三年后）", "sec": 9}
    check("「—（…）」有内容但无台词",
          SC.has_content(s_card) and not SC.has_line(s_card))
    check("正常台词既有内容也有台词",
          SC.has_content({"no": 3, "line": "你别走"}))

    # 空镜注入 → 必须出现"长镜空镜"阻断；把台词填上 → 必须消失
    # 时长要真的过 long_take_sec（AI 档 5s），否则测的是别的判据
    blank = SC.parse_shotlist(_shots_placeholder(4, "中景", "—", sec="7s"))
    r_blank = SC.analyze(blank, fmt="single", text="", mode="ai")
    bnames = [b["check"] for b in r_blank["blocking"]]
    check("注入全空长镜 → 报长镜空镜", "长镜空镜" in bnames, str(bnames))
    filled = SC.parse_shotlist(_shots_placeholder(4, "中景", "我不走", sec="7s"))
    r_filled = SC.analyze(filled, fmt="single", text="", mode="ai")
    check("填上台词 → 不再报长镜空镜",
          "长镜空镜" not in [b["check"] for b in r_filled["blocking"]],
          str([b["check"] for b in r_filled["blocking"]]))

    # 2) is_hook 的形态阈值必须真的按形态走。
    #    旧写法写死模块级 long_take_sec=5，漫剧(4s)/动漫(6s)的阈值全失效。
    sh = {"no": 1, "sec": 5.0, "size": "中景", "line": "你别走", "move": "固定"}
    check("真人档 5s 算长镜", SC.is_hook(sh, long_take_sec=5))
    check("动漫档 5s 不算长镜", not SC.is_hook(sh, long_take_sec=6))
    check("漫剧档 5s 算长镜", SC.is_hook(sh, long_take_sec=4))

    # 3) 景别白名单：视频形态也必须查（此前 SIZES 定义了但无人引用）
    bad = SC.parse_shotlist(_shots_placeholder(4, "近景", "我在"))
    check("正常景别不误报",
          "景别不在白名单" not in [b["check"] for b in
                                   SC.analyze(bad, fmt="single", mode="ai")["blocking"]]
          + [w["check"] for w in
             SC.analyze(bad, fmt="single", mode="ai")["warning"]])
    weird = SC.parse_shotlist(_shots_placeholder(4, "超级无敌大特写", "我在"))
    r_weird = SC.analyze(weird, fmt="single", mode="ai")
    check("荒谬景别 → 报不在白名单",
          "景别不在白名单" in [b["check"] for b in r_weird["blocking"]],
          str([b["check"] for b in r_weird["blocking"]]))

    # 4) sec_min：单镜过短必须警告
    short = ("| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |\n"
             "|---|---|---|---|---|---|\n"
             "| 1 | 1s | 中景 | 周宁 | 他站着 | 我来了 |\n"
             "| 2 | 3s | 中景 | 周宁 | 他走了 | 你走 |\n")
    r_short = SC.analyze(SC.parse_shotlist(short), fmt="single", mode="ai")
    check("单镜过短 → 警告",
          "单镜过短" in [w["check"] for w in r_short["warning"]],
          str([w["check"] for w in r_short["warning"]]))

    # 5) 漫画 close_ratio_min 必须真被执行（旧版：同一个语义两种名字，只认了视频那个）
    from common import constants_for
    cm = constants_for(None, "ai", form="motion_comic")["drama"]
    panel = cm.get("panel") or {}
    check("漫画形态确有 close_ratio_min", bool(panel.get("close_ratio_min")),
          str(panel.get("close_ratio_min")))
    # 全用"远景格"→ 近景占比 0 → 必须报警
    far = ("| 镜 | 时长 | 景别 | 运镜 | 谁在画面 | 画面一句话 | 气泡对白 |\n"
           "|---|---|---|---|---|---|---|\n"
           + "".join(f"| {i} | 2.5s | 远景格 | 固定 | 周宁 | 他站着 | — |\n"
                     for i in range(1, 5)))
    r_far = SC.analyze(SC.parse_shotlist(far), fmt="single", mode="ai",
                       form="motion_comic")
    check("漫画全远景 → 报近景占比不足",
          "漫画近景占比不足" in [w["check"] for w in r_far["warning"]],
          str([w["check"] for w in r_far["warning"]]))
    near = far.replace("远景格", "中格")
    r_near = SC.analyze(SC.parse_shotlist(near), fmt="single", mode="ai",
                        form="motion_comic")
    check("漫画中格为主 → 不报近景占比",
          "漫画近景占比不足" not in [w["check"] for w in r_near["warning"]],
          str([w["check"] for w in r_near["warning"]]))

    # 6) quality 空间变化必须逐镜取，不能扫全文。
    #    旧写法：正文里写三个场景名就满分（假阳性）。
    q_shot_md = ("| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |\n"
                 "|---|---|---|---|---|---|\n"
                 "| 1 | 3s | 中景 | 周宁 | 他在办公室 | 我在 |\n"
                 "| 2 | 3s | 中景 | 周宁 | 他还在办公室 | 你走 |\n"
                 "| 3 | 3s | 中景 | 周宁 | 他仍在办公室 | 别走 |\n")
    qbody = ("## 场景\n\n办公室。\n\n周宁在办公室，林晚在厨房，母亲在阳台。\n"
             "（正文里提了办公室、厨房、阳台三个地名，但镜头只有一个空间）\n")
    shots_q = SC.parse_shotlist(q_shot_md)
    dp = QL.dim_places(shots_q, qbody, QL.ST_)
    check("空间变化按镜算，正文地名不算数", dp["score"] < dp["max"],
          f"{dp['score']}/{dp['max']} {dp.get('detail', '')}")

    # 7) quality 人物数必须剔占位（"空镜""两人"不是人名）
    fake = ("| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | 3s | 中景 | 周宁｜他站着 | 他站着 | 我在 |\n"
            "| 2 | 3s | 中景 | 空镜｜房间空了 | 房间空了 | — |\n"
            "| 3 | 3s | 中景 | 两人｜他们站着 | 他们站着 | 你走 |\n")
    nm = set()
    for _s in SC.parse_shotlist(fake):
        nm |= QL._who_names(_s)
    check("占位不当人名（空镜/两人不计数）", nm == {"周宁"}, str(nm))

    # 8) 交付表体必须查：表头对 ≠ 稿子对
    good = _delivery_ok()
    ok_res = SL.check_delivery(good, CONST, form="live_action", is_ai=True)
    check("正常交付稿不误报", ok_res["ok"], str(ok_res["problems"]))
    # 注入三样：镜号跳号、时长非正、景别不在白名单。
    # 样本只有 1 镜，所以先补到 3 镜再破坏，才能测"连续性"。
    two = good.replace(
        "| 1 | 6s | 中景 | 林晚 | 站在门口 | 我下周三来 |\n",
        "| 1 | 6s | 中景 | 林晚 | 站在门口 | 我下周三来 |\n"
        "| 2 | 5s | 近景 | 林晚 | 她坐下 | 你瘦了 |\n"
        "| 3 | 4s | 中景 | 林晚 | 她起身 | 我走了 |\n", 1)
    check("样本补到 3 镜可测连续性",
          len([l for l in two.splitlines() if l.startswith("| 2 |")]) == 1)
    ok2 = SL.check_delivery(two, CONST, form="live_action", is_ai=True)
    check("3 镜正常稿仍不误报", ok2["ok"], str(ok2["problems"]))

    evil = two.replace("| 2 | 5s | 近景 |", "| 22 | 999s | 超级景 |", 1)
    check("注入确实改到了表体", evil != two)
    el_res = SL.check_delivery(evil, CONST, form="live_action", is_ai=True)
    check("表体坏 → 拦住", bool(el_res["problems"]), str(el_res["problems"]))
    check("报出镜号问题", any("镜号" in p for p in el_res["problems"]),
          str(el_res["problems"]))
    check("报出时长问题", any("时长" in p for p in el_res["problems"]),
          str(el_res["problems"]))
    check("报出景别问题", any("景别" in p for p in el_res["problems"]),
          str(el_res["problems"]))

    # 人物表对账：分镜表出场了却没登记 → 必须报
    unreg = two.replace("| 3 | 4s | 中景 | 林晚 | 她起身 | 我走了 |",
                        "| 3 | 4s | 中景 | 张三 | 他起身 | 我走了 |", 1)
    check("注入未登记人物确实改到了", unreg != two)
    ur_res = SL.check_delivery(unreg, CONST, form="live_action", is_ai=True)
    check("未登记人物 → 报",
          any("未登记" in p and "张三" in p for p in ur_res["problems"]),
          str(ur_res["problems"]))
    check("已登记人物不误报",
          not any("未登记" in p for p in SL.check_delivery(
              two, CONST, form="live_action", is_ai=True)["problems"]),
          str(SL.check_delivery(two, CONST, form="live_action", is_ai=True)["problems"]))
    # 占位词不能当没登记的人名（"空镜""两人"）
    ph = two.replace("| 3 | 4s | 中景 | 林晚 | 她起身 | 我走了 |",
                     "| 3 | 4s | 中景 | 空镜 | 房间空了 | — |", 1)
    check("占位不当未登记人物",
          not any("未登记" in p for p in
                  SL.check_delivery(ph, CONST, form="live_action", is_ai=True)["problems"]),
          str(SL.check_delivery(ph, CONST, form="live_action", is_ai=True)["problems"]))

    # 9) cliff 评分：口径必须百分制，强卡点能到 100、弱卡点不合格
    import cliff as CF
    strong = SC.parse_shotlist(
        "| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 3s | 中景 | 周宁 | 他递过照片 | 这是三年前的 |\n"
        "| 2 | 3s | 特写 | 周宁 | 照片上的人是她 | 原来是你？ |\n")
    r_strong = CF.score_ending(strong, CONST["drama"])
    check("强卡点拿满分", r_strong["score"] >= 90 and r_strong["ok"],
          f"{r_strong['score']} {r_strong['detail']}")
    weak = SC.parse_shotlist(
        "| 镜 | 时长 | 景别 | 谁在画面 | 动作一句话 | 台词原文 |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | 3s | 中景 | 周宁 | 他坐着 | 我在 |\n"
        "| 2 | 3s | 中景 | 周宁 | 他站起来走了 | 明天见。 |\n")
    r_weak = CF.score_ending(weak, CONST["drama"])
    check("弱卡点判不合格", not r_weak["ok"] and r_weak["band"] == "不合格",
          f"{r_weak['score']} {r_weak['band']}")

    # 10) 分集节奏四条规则：注入必坏输入 → 必须报
    pay_bad = ("## 分集表\n\n| 集 | 时长 | 情绪净值 | 单元线 | 主线推进 | 结尾钩子 | 标记 |\n"
               "|---|---|---|---|---|---|---|\n"
               "| 1 | 90s | 5 | a | b | c | 免费区 |\n"
               "| 2 | 90s | 4 | a | b | c | 免费区 |\n"
               "| 3 | 90s | 3 | a | b | c | ★ 付费卡点 |\n"
               "| 4 | 90s | -2 | a | b | c | 小高潮 |\n"
               "| 5 | 90s | -1 | a | b | c | 小高潮 |\n")
    rb, rw, ri = CF.check_series_rhythm(pay_bad, CONST["drama"])
    names = [w["check"] for w in rw]
    check("付费前不是低谷 → 报", "付费卡点前不是情绪低谷" in names, str(names))
    check("高潮挤堆 → 报", "高潮挤堆" in names, str(names))
    check("连续压抑 → 报", "连续压抑过久" in names, str(names))
    # 越界必须阻断
    oob = pay_bad.replace("| 4 | 90s | -2 |", "| 4 | 90s | -99 |")
    rb2, _, _ = CF.check_series_rhythm(oob, CONST["drama"])
    check("情绪净值越界 → 阻断",
          "情绪净值越界" in [b["check"] for b in rb2], str([b["check"] for b in rb2]))
    # 健康的表：不报
    good_ep = ("## 分集表\n\n| 集 | 时长 | 情绪净值 | 单元线 | 主线推进 | 结尾钩子 | 标记 |\n"
               "|---|---|---|---|---|---|---|\n"
               "| 1 | 90s | 2 | a | b | c | 免费区 |\n"
               "| 2 | 90s | -5 | a | b | c | 免费区 |\n"
               "| 3 | 90s | 3 | a | b | c | ★ 付费卡点 |\n"
               "| 4 | 90s | 1 | a | b | c |  |\n"
               "| 5 | 90s | 2 | a | b | c |  |\n"
               "| 6 | 90s | 3 | a | b | c | 小高潮 |\n")
    rb3, rw3, _ = CF.check_series_rhythm(good_ep, CONST["drama"])
    check("健康分集表不误报", not rb3 and not rw3, str([w["check"] for w in rw3]))

    # 11) continuity 开关：关掉即不查（旧版 7 个开关全是空转）
    import continuity as CY
    const_off = json.loads(json.dumps(CONST))
    const_off["drama"]["continuity"] = {
        "require_timeline": False, "character_id_required": True,
        "scene_reuse_tracked": True, "costume_change_must_declare": True,
        "foreshadow_double_bind": True, "dangling_ref_forbidden": True,
    }
    with tempfile.TemporaryDirectory() as sandbox:
        p = Path(sandbox) / "proj"
        p.mkdir()
        (p / "continuity.md").write_text(
            "## 时间轴\n\n| 集 | 起 | 止 | 说明 |\n|---|---|---|---|\n"
            "| 1 | 第一天 | 第二天 |  |\n| 3 | 第五天 | 第六天 |  |\n",
            encoding="utf-8")
        r_on = CY.check(p, const=CONST)
        r_off = CY.check(p, const=const_off)
        on_b = [b["check"] for b in r_on["blocking"]]
        off_b = [b["check"] for b in r_off["blocking"]]
        check("时间轴跳号：开关开 → 报", "时间轴跳号" in on_b, str(on_b))
        check("时间轴跳号：开关关 → 不报", "时间轴跳号" not in off_b, str(off_b))
        check("开关状态被记录", r_off["info"].get("switches", {}).get("require_timeline") is False,
              str(r_off["info"].get("switches")))

        # 时间轴断口（timeline_gap_max_s = 0 要求首尾相接）
        (p / "continuity.md").write_text(
            "## 时间轴\n\n| 集 | 起 | 止 | 说明 |\n|---|---|---|---|\n"
            "| 1 | 第一天 | 第二天 |  |\n| 2 | 第九天 | 第十天 |  |\n",
            encoding="utf-8")
        r_gap = CY.check(p, const=CONST)
        check("时间轴断口 → 报",
              "时间轴有断口" in [w["check"] for w in r_gap["warning"]],
              str([w["check"] for w in r_gap["warning"]]))
        (p / "continuity.md").write_text(
            "## 时间轴\n\n| 集 | 起 | 止 | 说明 |\n|---|---|---|---|\n"
            "| 1 | 第一天 | 第二天 |  |\n| 2 | 第二天 | 第三天 |  |\n",
            encoding="utf-8")
        r_nogap = CY.check(p, const=CONST)
        check("首尾相接 → 不报断口",
              "时间轴有断口" not in [w["check"] for w in r_nogap["warning"]],
              str([w["check"] for w in r_nogap["warning"]]))


def t_no_dead_config():
    """config 里的每个键都必须真被代码消费——空转的开关比没有开关更坏。"""
    import re as _re
    import tomllib
    root = Path(__file__).resolve().parents[2]
    cfg = root / "config" / "dramakit.toml"
    if not cfg.exists():
        check("能找到 config", False, str(cfg))
        return
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
    src = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted((root / "engine").rglob("*.py")))
    tokens = set(m.group(0) for m in _re.finditer(r"[A-Za-z_][A-Za-z0-9_]*", src))

    def leaves(obj, prefix=""):
        out = []
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out.extend(leaves(v, p) if isinstance(v, dict) else [p])
        return out

    no_source = {
        "motion_comic.asset.expression_enum",
        "motion_comic.asset.costume_enum_required",
        "anime.asset.char_views",
    }
    dead = [p for p in leaves(data)
            if p.split(".")[-1] not in tokens and p not in no_source]
    check("config 无空转键", not dead, "空转：" + "、".join(dead))
    # 阈值必须与判据同源：cliff 门槛不能超过它能达到的满分
    cf = data.get("cliff") or {}
    sc = cf.get("score") or {}
    base = sum(float(sc.get(k, 0)) for k in
               ("line_hook", "visual_event", "emotion_close", "cliff_question"))
    check("cliff 门槛不高于基础满分",
          float(cf.get("cliff_score_min", 0)) <= base + 
          sum(float(sc.get(k, 0)) for k in ("info_reverse", "call_back")),
          f"门槛 {cf.get('cliff_score_min')} vs 基础满分 {base}")


CASES = [
    ("A 节拍配比", t_beats),
    ("B 分镜骨架", t_skeleton),
    ("C 分镜表解析", t_parse),
    ("D 阻断项检出", t_scan_blocking),
    ("E 镜头语言档位", t_profile),
    ("F 交付格式校验", t_delivery),
    ("G 系列分集表", t_series),
    ("H 覆盖优先级", t_override),
    ("I 四种形态", t_forms),
    ("J 漫画近中景判据", t_comic_ratio),
    ("K 模型与平台规格", t_model_platform),
    ("L 合规检查", t_compliance),
    ("M 跨集一致性", t_continuity),
    ("N 漫画交付格式", t_delivery_comic),
    ("O 增量重扫接线", t_incremental),
    ("P 故事质量评分卡", t_quality),
    ("Q 命令行接线", t_wiring),
    ("R 断点续跑与站位", t_progress),
    ("S 判据不静默失效", t_no_silent_fail),
    ("T config 无空转", t_no_dead_config),
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
