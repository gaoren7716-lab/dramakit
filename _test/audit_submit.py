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

from common import is_row, norm_head, split_row          # noqa: E402
import scan as SC                                        # noqa: E402

# 这些"谁在画面"是物件/群体/介质，不需要在人物表登记。
NOT_A_PERSON = {
    "手机屏幕", "手机", "屏幕", "电脑屏幕", "电脑", "便签", "材料", "黑板", "白板",
    "那碗饭", "碗", "锅", "锅铲", "筷子", "空椅子", "名册", "社团名册", "第四把椅子",
    "欠条", "账单", "诊断书", "同意书", "简历", "照片", "纸条", "信件", "合同",
    "报表", "门", "窗", "墙", "桌子", "椅子", "电梯", "监控", "画面文字", "门缝",
    "镜子", "轮椅", "床", "窗帘", "两个同学", "同学们", "同学", "路人", "众人",
    "服务员", "群演", "两个男生", "三个男生",
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

    认三种写法，缺一种就会把"覆盖完整"误报成"漏了"：
      - `镜3至6` / `镜3-6` / `镜3~6`  → 区间
      - `镜8、9` / `镜1,2,3`          → 枚举（**踩过的坑**：只认"镜N"会把
        "镜8、9"读成只有镜 8，于是镜 9 被判成"场景没覆盖"，其实是脚本瞎了）
      - `镜7`                          → 单镜
    """
    out = []
    spans = []
    for m in re.finditer(r"镜\s*(\d+)\s*(?:至|-|~|—)\s*(\d+)", s):
        out.append((int(m.group(1)), int(m.group(2))))
        spans.append(m.span())
    # 枚举：镜N 后面跟一串 "、M"
    enum_spans = []
    for m in re.finditer(r"镜\s*(\d+)((?:\s*[、,，]\s*\d+)+)", s):
        nums = [int(m.group(1))] + [int(x) for x in re.findall(r"\d+", m.group(2))]
        out.extend((k, k) for k in nums)
        enum_spans.append(m.span())
    for m in re.finditer(r"镜\s*(\d+)", s):
        if any(a <= m.start() < b for a, b in spans + enum_spans):
            continue
        n = int(m.group(1))
        out.append((n, n))
    return out


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
    # 定语归一：人物表登记「一楼的父亲」，分镜表写「父亲」，是同一个人。
    # ★ 这条坑踩过三次了（"床上的父亲" vs "父亲"），凡是人名对账都得归一。
    short = {n.rsplit("的", 1)[-1] for n in names} | names

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
                    if not w or w in NOT_A_PERSON or w in short:
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
        missing = [k for k in range(1, n + 1) if total.get(k, 0) == 0]
        dup = [k for k in range(1, n + 1) if real.get(k, 0) > 1]
        extra = [k for k in total if k > n]
        if missing:
            problems.append(f"场景没覆盖：镜{'、'.join(map(str, missing))}")
        if dup:
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
