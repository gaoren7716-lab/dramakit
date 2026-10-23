# -*- coding: utf-8 -*-
"""提交版内部自洽普查（老板模板格式）。

查四样，都是"同一份稿子里两处信息对不上"的机械可判问题：

1. **人物表 vs 谁在画面**：出场的人必须在人物表登记（物件/群体除外）。
2. **场景的镜号范围 vs 分镜表**：每个镜号必须**恰好被一个场景覆盖**。
   漏了 → 这场戏不知道在哪拍；重了 → 同一镜两个地点，生成端二选一。
3. **服装锁定的镜号 vs 分镜表**：写了"镜 9"但没有第 9 镜，是写错了。
4. **人物表的行数**：每人必须 4 行（姓名/长相/服装/道具），少一行就是漏了锁定。

★ 为什么值得单独查：这四样**单看每一处都合理**，只有把两处放在一起才看得出矛盾。
  「内部不能矛盾」是老板模板写在人物那一节的原话，但矛盾从来不在同一节里。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine" / "lib"))

from common import is_object_name, is_row, norm_head, split_row   # noqa: E402
import scan as SC                                        # noqa: E402

# 这些"谁在画面"是物件/群体/介质，不需要在人物表登记。
NOT_A_PERSON = {
    "手机屏幕", "手机", "屏幕", "电脑屏幕", "电脑", "便签", "材料", "黑板", "白板",
    "那碗饭", "碗", "锅", "锅铲", "筷子", "空椅子", "名册", "社团名册", "第四把椅子",
    "欠条", "账单", "诊断书", "同意书", "简历", "照片", "纸条", "信件", "合同",
    "报表", "门", "窗", "墙", "桌子", "椅子", "电梯", "监控", "画面文字", "门缝",
    "镜子", "轮椅", "床", "窗帘", "两个同学", "同学们", "同学", "路人", "众人",
    "服务员", "群演", "两个男生", "三个男生",
    # ★ 与 shotlist.WHO_STOP / quality.WHO_NOT_A_NAME 三处同步。
    #   三处不同步的后果：check 说不报、audit 说报，然后没人再信任何一边。
    "八仙桌", "桌面", "床头柜", "收银台", "柜台", "台面",
    "判决书", "协议书", "通报文件", "通报", "文件", "单据", "发票",
    "账本", "病历", "房产证", "菜单", "作业本", "点单本",
    # ★ 第三批（实测报出来的）：物件表是知识缺口，不是规则。
    "门牌", "工资条", "告示牌", "招牌", "推床", "输液架", "病历本", "挂号单",
    "笔录", "询问笔录", "签字单",
}


def _block(text: str, name: str) -> str:
    m = re.search(rf"^#+\s*(?:\d+\s*[、.．]?\s*)?{name}[^\n]*$(.*?)(?=^#|\Z)",
                  text, re.M | re.S)
    return m.group(1) if m else ""


def _shot_rows(text: str) -> list[list[str]]:
    head, rows = None, []
    for ln in text.splitlines():
        if not is_row(ln):
            continue
        cells = [norm_head(c) for c in split_row(ln)]
        if head is None:
            if cells and cells[0] in ("镜", "镜号"):
                head = cells
            continue
        if cells and cells[0].isdigit():
            rows.append(cells)
    return rows


def _ranges(s: str) -> list[tuple[int, int]]:
    """从一段文字里抠出所有镜号区间。

    写法比想象的多，少认一种就把"覆盖完整"误报成"漏了"：
      - `镜3至6` / `镜3-6` / `镜3~6`   → 区间
      - `镜8、9` / `镜1,2,3`           → 枚举
      - `镜1至5、7至9`                 → **区间与枚举混排**
      - `镜7`                           → 单镜

    ★ 踩过的坑（两次，同一根）：
      第一版只认 `镜N`，于是「镜8、9」读成只有镜 8，镜 9 被判"没覆盖"。
      第二版加了枚举，但枚举正则要求每项都带"镜"字前缀，于是
      「镜1至5、7至9」里的 `7至9` 没有"镜"字，整段只读出 (1,5)，
      镜 7、8、9 全被判"没覆盖"——稿子是对的，脚本瞎了。

    ★ 所以现在不按"每个正则各自扫全文"写，而是**先按"镜"字切段，
      段内再按顿号切项**。一条"镜"字管它后面的一串，这才是人写这句话的语义。
    """
    out: list[tuple[int, int]] = []
    starts = [m.start() for m in re.finditer(r"镜", s)]
    for i, st in enumerate(starts):
        seg = s[st + 1: starts[i + 1] if i + 1 < len(starts) else len(s)]
        # ★ 只取**紧跟着"镜"字的那一串镜号**。遇到第一个不是数字/顿号/逗号/
        #   区间符的字符就截断——因为从那里往后已经不是镜号了。
        #   不截断的后果（实测踩到）：
        #   「健身卡（镜1、5、8、9），卡面印着有效期至2029年」
        #   里的 2029 被当成镜号，报「写到镜2029，但全片只有 9 镜」。
        #   更阴的是「至」字：它既是区间连接符，也是普通汉字（有效期至）。
        head = re.match(r"[\d\s、,，至~—\-]*", seg)
        seg = head.group(0) if head else ""
        if not seg:
            continue
        for part in seg.split("|"):
            for item in re.split(r"[、,，]", part):
                item = item.strip()
                # 以连接符开头的残片（「至2029」这种）不是镜号，丢掉
                if not item or not item[0].isdigit():
                    continue
                rng = re.fullmatch(r"(\d+)\s*(?:至|~|—|-)\s*(\d+)", item)
                if rng:
                    out.append((int(rng.group(1)), int(rng.group(2))))
                    continue
                for num in re.findall(r"\d+", item):
                    out.append((int(num), int(num)))
    return out


def _same_person(who: str, names: set[str]) -> bool:
    """「谁在画面」写的这个称呼，是否指向人物表里的某个人。

    人物表登记「银行柜员」，分镜表写「柜员」——同一个人。
    登记「一楼的父亲」，写「父亲」——也是同一个人。

    认三种等价：完全相同 / 去掉定语（`X的Y` → `Y`）/ 是某人名的**后缀**。
    ★ 后缀长度必须 ≥2：只留一个字的匹配会乱撞（「老王」和「王」不是一回事）。
    """
    if who in names:
        return True
    if who.rsplit("的", 1)[-1] in names:
        return True
    if len(who) >= 2:
        for n in names:
            if n != who and n.endswith(who):
                return True
    return False



def audit(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    rows = _shot_rows(text)
    n = len(rows)
    problems = []

    # 1. 人物表行数：每人 4 行
    people_blk = _block(text, "(?:人物表|人物)")
    people = []
    cur = None
    for ln in people_blk.splitlines():
        s = ln.strip()
        if s.startswith("姓名｜年龄｜身份") or s.startswith("姓名|年龄|身份"):
            if cur:
                people.append(cur)
            cur = {"name": s.split("：", 1)[-1].split("｜")[0].strip(), "lines": 1}
        elif cur and re.match(r"^(长相锁定|服装锁定|贯穿道具)", s):
            cur["lines"] += 1
    if cur:
        people.append(cur)
    for p in people:
        if p["lines"] != 4:
            problems.append(f"人物「{p['name']}」只有 {p['lines']} 行，模板要求 4 行")

    names = {p["name"] for p in people}

    # 2. 谁在画面 vs 人物表
    if rows:
        wi = None
        for ln in text.splitlines():
            if is_row(ln) and norm_head(split_row(ln)[0]) in ("镜", "镜号"):
                head = [norm_head(c) for c in split_row(ln)]
                if "谁在画面" in head:
                    wi = head.index("谁在画面")
                break
        if wi is not None:
            for cells in rows:
                if len(cells) <= wi:
                    continue
                for w in re.split(r"[·、,，/／+＋和与\s]+", cells[wi]):
                    w = w.strip("（）()【】[]")
                    if not w or w in NOT_A_PERSON or is_object_name(w) or _same_person(w, names):
                        continue
                    if re.fullmatch(r"[\d.]+s?", w):
                        continue
                    problems.append(f"镜{cells[0]}「谁在画面」里的「{w}」不在人物表")

    # 3. 场景镜号覆盖。
    #    ★ 只扫**地点那一格**（第一个 ｜ 之前）。整行扫会把「时间」格里
    #      "镜5白天／镜7夜里"的镜号也算成认领，于是同一镜被"两个场景"抢——
    #      那是脚本瞎了，不是稿子错了。
    #    ★ 「画外空间」「屏幕里的画面」是叠在实景上的第二层，不参与"抢镜"判定；
    #      只被叠层覆盖的镜头（纯屏幕插入）是合法的，不报"没覆盖"。
    scene_blk = _block(text, "场景")
    real, total = {}, {}
    for ln in scene_blk.splitlines():
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        place = s.split("｜")[0] if "｜" in s else s
        overlay = ("画外" in place or "屏幕里的" in place or "视频通话" in place)
        for a, b in _ranges(place):
            for k in range(a, b + 1):
                total[k] = total.get(k, 0) + 1
                if not overlay:
                    real[k] = real.get(k, 0) + 1
    if n:
        # ★ 一镜到底（oner）：**一个镜头穿过多于一个空间，是形式定义，不是抢镜。**
        #   电梯跟到楼道，全片只有这一个镜头，但它经过两个地点。
        #   判定条件与 shotlist 一致：声明「一镜到底：是」且全片只有 1 镜。
        #   只认声明不认镜数，就等于给了一个万能开关——写一句话绕过所有覆盖检查。
        oner = n == 1 and bool(re.search(r"一镜到底\s*[:：]\s*是", text))
        missing = [k for k in range(1, n + 1) if total.get(k, 0) == 0]
        dup = [k for k in range(1, n + 1) if real.get(k, 0) > 1]
        extra = [k for k in total if k > n]
        if missing:
            problems.append(f"场景没覆盖：镜{'、'.join(map(str, missing))}")
        if dup and not oner:
            problems.append(f"同一镜被两个实景同时认领：镜{'、'.join(map(str, dup))}")
        if extra:
            problems.append(f"场景写了不存在的镜号：镜{'、'.join(map(str, sorted(extra)))}")

    # 4. 服装/道具里的镜号必须存在
    for ln in people_blk.splitlines():
        s = ln.strip()
        if not (s.startswith("服装锁定") or s.startswith("贯穿道具")):
            continue
        for a, b in _ranges(s):
            for k in (a, b):
                if k > n:
                    problems.append(f"「{s[:6]}」写到镜{k}，但全片只有 {n} 镜")
    return problems


BROKEN = """# 坏样本 · 提交版

## 1. 基本信息（一行一项）

画幅时长：竖屏9:16，约12秒，3镜

## 2. 人物（每人4行，写死，内部不能矛盾）

姓名｜年龄｜身份：周宁｜30岁｜测试员
长相锁定：短发
服装锁定：灰衬衫（镜1至3）
贯穿道具：无

## 3. 场景（每场景一段）

出租屋（镜1、3）｜夜｜冷白｜一张桌子

## 4. 分镜表

镜号｜目标时长｜景别｜谁在画面｜动作一句话｜台词原文（无则写无）
01｜4s｜近景｜周宁｜他坐下｜无
02｜4s｜近景｜阿鬼｜他抬头｜无
03｜4s｜近景｜周宁｜他起身｜无

## 5. 验收与额度（一行定死）

重抽上限：每镜最多1次
"""


def selftest() -> int:
    """注入式证伪：造一份**必坏**的稿子，确认四条判据都会红。

    ★ 只测"正常稿子能过"的自检是假保护——它证明不了判据会响。
    """
    import tempfile
    fails = []

    def want(name, cond, detail=""):
        print(f"  {'✓' if cond else '✗'} {name}" + (f"  [{detail}]" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    # 坏样本：镜2 的人没登记、镜2 没场景、服装写了镜9（只有3镜）
    bad = BROKEN.replace("服装锁定：灰衬衫（镜1至3）", "服装锁定：灰衬衫（镜1至9）")
    with tempfile.TemporaryDirectory() as sandbox:
        p = Path(sandbox) / "bad-提交版.md"
        p.write_text(bad, encoding="utf-8")
        ps = audit(p)
    joined = "\n".join(ps)
    want("注入·未登记的人被查出", "阿鬼" in joined, joined)
    want("注入·漏覆盖的镜被查出", "镜2" in joined, joined)
    want("注入·不存在的镜号被查出", "镜9" in joined, joined)

    # 正常样本：把坏样本修好，必须一条都不报
    good = (BROKEN.replace("阿鬼", "周宁")
                  .replace("出租屋（镜1、3）", "出租屋（镜1至3）"))
    with tempfile.TemporaryDirectory() as sandbox:
        p = Path(sandbox) / "good-提交版.md"
        p.write_text(good, encoding="utf-8")
        ps2 = audit(p)
    want("修好之后不再报", not ps2, "\n".join(ps2))

    # 枚举写法不能被读瞎（"镜1、3" 要认成 1 和 3，不是只有 1）
    want("枚举镜号解析", _ranges("出租屋（镜1、3）") == [(1, 1), (3, 3)],
         str(_ranges("出租屋（镜1、3）")))
    want("区间镜号解析", _ranges("镜2至5") == [(2, 5)], str(_ranges("镜2至5")))
    # ★ 区间与枚举混排。踩过：第二版枚举正则要求每项都带"镜"字，
    #   于是「镜1至5、7至9」只读出 (1,5)，镜 7、8、9 被判"没覆盖"。
    want("混排镜号解析", _ranges("客厅（镜1至5、7至9）") == [(1, 5), (7, 9)],
         str(_ranges("客厅（镜1至5、7至9）")))
    want("混排含单镜", _ranges("镜1至3、5、7至9") == [(1, 3), (5, 5), (7, 9)],
         str(_ranges("镜1至3、5、7至9")))
    # ★ 人名后缀归一。踩过：人物表登记「银行柜员」，分镜表写「柜员」，
    #   被当成"没登记的人"报出来——是判据不认人话。
    want("人名后缀归一", _same_person("柜员", {"银行柜员", "宋长河"}),
         "「柜员」应认作「银行柜员」")
    want("人名定语归一", _same_person("父亲", {"一楼的父亲"}),
         "「父亲」应认作「一楼的父亲」")
    want("单字不归一", not _same_person("王", {"老王"}),
         "「王」不能认作「老王」——单字会乱撞")
    want("不同的人不归一", not _same_person("护工", {"蒋德海", "蒋晓梅"}),
         "「护工」不在人物表就是不在")
    # ★ 物件后缀规则。踩过：白名单只有「协议书」，稿子写「承揽协议书」，
    #   被判成"这个人没登记"。判据不认人话，然后被整条关掉。
    want("物件完全匹配", is_object_name("判决书"), "「判决书」是物件")
    want("物件后缀匹配", is_object_name("承揽协议书"), "「承揽协议书」应以「协议书」后缀命中")
    # ★ 尾字规则。全名表永远补不完——每写一部新剧就多出「电子屏」「打卡机」
    #   「货车」「解除通知书」，靠一个个往表里补，补的速度追不上写的速度，
    #   而每次漏报都会逼人把整条判据关掉。
    want("物件尾字匹配",
         is_object_name("电子屏") and is_object_name("打卡机")
         and is_object_name("货车") and is_object_name("解除通知书"),
         "屏/机/车/书 结尾的应认作物件")
    # ★ 反向：尾字必须排除会跟人名撞车的那一批。
    #   「军」「平」「海」都是常见人名尾字，收进尾字表会把真人判成物件——
    #   那是比漏报更坏的错：**漏报看得见，误杀看不见**。
    want("人名尾字不误杀",
         not any(is_object_name(n) for n in ("张军", "李平", "王海", "刘伟", "陈明")),
         "军/平/海/伟/明 是常见人名尾字，不能判成物件")
    want("人名不是物件", not is_object_name("周长顺"), "「周长顺」是人")
    want("单字不判物件", not is_object_name("书"), "单字不做后缀匹配")
    # ★ 「至」既是区间连接符又是普通汉字。踩过：道具写
    #   「健身卡（镜1、5、8、9），卡面印着有效期至2029年」，
    #   2029 被当成镜号，报「写到镜2029，但全片只有 9 镜」。
    want("年份不当镜号",
         _ranges("健身卡（镜1、5、8、9），卡面印着有效期至2029年")
         == [(1, 1), (5, 5), (8, 8), (9, 9)],
         str(_ranges("健身卡（镜1、5、8、9），卡面印着有效期至2029年")))
    want("规格数字不当镜号",
         _ranges("同意书（镜1、3），A4纸，边角被捏软了") == [(1, 1), (3, 3)],
         str(_ranges("同意书（镜1、3），A4纸，边角被捏软了")))

    # ★ 一镜到底：一个镜头穿过多于一个空间，是**形式定义**，不是抢镜。
    #   但这条放宽必须同时证明"别放得太宽"——只测能过等于给自己开后门。
    oner_doc = (
        "# 测试剧\n\n"
        "## 1. 基本信息（一行一项）\n\n"
        "画幅时长：竖屏9:16，约60秒，1镜\n"
        "一镜到底：是（整部只有1个镜头，全程不切）\n\n"
        "## 2. 人物（每人4行）\n\n"
        "姓名｜年龄｜身份：周海｜28岁｜测试员\n"
        "长相锁定：寸头\n服装锁定：蓝工服（镜1）\n贯穿道具：手机（镜1）\n\n"
        "## 3. 场景（每场景一段）\n\n"
        "电梯轿厢（镜1）｜深夜｜冷白顶灯｜按键面板\n"
        "13楼楼道（镜1）｜深夜｜声控灯｜垃圾袋\n\n"
        "## 4. 分镜表\n\n"
        "镜号｜目标时长｜景别｜谁在画面｜动作一句话｜台词原文（无则写无）\n"
        "01｜60s｜全景｜周海｜他进电梯按13楼｜无\n"
    )
    with tempfile.TemporaryDirectory() as sandbox:
        p = Path(sandbox) / "oner-提交版.md"
        p.write_text(oner_doc, encoding="utf-8")
        ps_oner = audit(p)
    want("oner·跨空间不报抢镜", not any("两个实景" in x for x in ps_oner),
         "\n".join(ps_oner))

    # 去掉声明：同样的场景写法必须照旧红——放宽只属于 oner。
    with tempfile.TemporaryDirectory() as sandbox:
        p = Path(sandbox) / "not-oner-提交版.md"
        p.write_text(oner_doc.replace("一镜到底：是（整部只有1个镜头，全程不切）\n", ""),
                     encoding="utf-8")
        ps_plain = audit(p)
    want("非oner·跨空间仍报抢镜", any("两个实景" in x for x in ps_plain),
         "\n".join(ps_plain))

    print(f"\n{len(fails)} 项失败" if fails else "\n全部通过")
    return 1 if fails else 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    root = Path(sys.argv[1] if len(sys.argv) > 1
                else r"C:/Users/gaore/workbuddy-ai/小说/短剧")
    files = sorted(root.glob("*/*-提交版.md"))
    print(f"提交版文件：{len(files)} 份")
    bad = 0
    for f in files:
        ps = audit(f)
        if ps:
            bad += 1
            print(f"\n✗ {f.name}")
            for p in ps:
                print("   ·", p)
    print(f"\n有问题：{bad} 份 / {len(files)} 份")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
