#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧工坊 · 命令行入口

    python engine/dramakit.py new  我的剧 "一句话灵感" [--format single|series] [--form ...]
    python engine/dramakit.py plan 我的剧 [--episode] [--platform 名]
    python engine/dramakit.py skel 我的剧 [--write]
    python engine/dramakit.py scan 我的剧/分镜表.md [--mode ai|live] [--form ...] [--json]
    python engine/dramakit.py check 我的剧/我的剧.md
    python engine/dramakit.py continuity 我的剧
    python engine/dramakit.py forms
    python engine/dramakit.py spec [--platform 名] [--model 名]
    python engine/dramakit.py quality 我的剧/我的剧.md
    python engine/dramakit.py progress 我的剧
    python engine/dramakit.py selftest
    python engine/dramakit.py doctor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import (C, CONST, FORMS, PROJECTS_DIR, constants_for, ensure_utf8,
                    find_project_root, model_for, platform_for)  # noqa: E402

FORM_NAME = {"live_action": "真人/AI 写实短剧", "anime": "动漫短剧",
             "motion_comic": "漫剧（动态漫）", "static_comic": "静态漫 / 条漫"}



def parse_changed(spec: str) -> list:
    """把 "3,5" 或 "3-7" 解析成镜号列表。"""
    out = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return sorted(set(out))


def resolve_project(name: str) -> Path:
    p = Path(name)
    if p.exists():
        return p.resolve()
    q = PROJECTS_DIR / name
    if q.exists():
        return q
    raise SystemExit(f"找不到项目：{name}（也不在 {PROJECTS_DIR}）")


# ------------------------------------------------------------
# new
# ------------------------------------------------------------

def _write_plan(files: dict, root: Path, dry: bool) -> int:
    """干跑：先把"要写哪些文件、各多大"说清楚，再决定写不写。

    为什么默认要过这一道：写盘是**不可逆**的动作，而错误往往不是内容错，
    是"写到了不该写的地方"（同名项目覆盖、路径拼错）。先打印清单，
    一眼就能看出不对——这比事后 git diff 便宜得多。
    ★ 本项目不调模型、不产生费用，所以"计费双确认"在这里不成立，
      只吸收"写盘前先干跑"这一半，且老实写在文档里。
    """
    total = sum(len(t.encode("utf-8")) for t in files.values())
    print(C.bold(f"\n  将写入 {len(files)} 个文件，共 {total} 字节"))
    for p, t in files.items():
        try:
            rel = p.relative_to(root.parent)
        except ValueError:
            rel = p
        print(f"    {rel}   {len(t.encode('utf-8'))} B")
    if dry:
        print(C.dim("\n  干跑：未写盘。确认无误后去掉 --dry-run 再跑一次。"))
        return 0
    root.mkdir(parents=True, exist_ok=True)
    for p, t in files.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(t, encoding="utf-8")
    return 1


def cmd_new(args):
    import plan as PL
    import progress as PG
    import shotlist as SL

    name = args.project
    fmt = args.format or "single"
    frm = args.form or "live_action"
    proj = PROJECTS_DIR / name
    if proj.exists() and not args.force:
        raise SystemExit(f"项目已存在：{proj}（要重建加 --force）")

    meta = {"name": name, "genre": args.genre, "format": fmt, "form": frm,
            "version": CONST["drama"]["meta"]["version"]}

    # 骨架与列头必须跟随形态：漫剧形态要用"中格"与气泡列头，
    # 直接取全局 CONST 会生成"中景 / 动作一句话"的视频表（实测 bug）。
    const = constants_for(proj, form=frm)
    files = {
        proj / "project.json": json.dumps(meta, ensure_ascii=False, indent=2),
        proj / "README.md": SL.project_readme(name, args.genre, fmt, form=frm),
    }
    if fmt == "series":
        n = args.episodes or CONST["drama"]["series"]["episodes_default"]
        files[proj / "分集表.md"] = PL.episodes_md(n, const) + "\n"
        files[proj / "第01集-分镜表.md"] = (
            SL.render_md(SL.skeleton(const), const=const) + "\n")
        # 连载项目同时给一份跨集台账骨架，否则写到第 20 集
        # 就没人记得第 3 集登记了什么
        import continuity as CY
        files[proj / "continuity.md"] = CY.TEMPLATE.format(name=name) + "\n"
    else:
        files[proj / "节拍表.md"] = PL.beats_md(const) + "\n"
        files[proj / "分镜表.md"] = SL.render_md(SL.skeleton(const), const=const) + "\n"

    if not _write_plan(files, proj, args.dry_run):
        return 0
    # 空目录不写进计划里（没有内容可写），但它是给 overrides.toml 留的位置
    (proj / "config").mkdir(parents=True, exist_ok=True)

    # 立项只记"结构文件"的指纹。把节拍表/分镜表也记进来是错的：
    # 那两张表后面一定要改，一改就把"立项"标成产物变更，噪音淹掉真信号。
    PG.record(proj, "new", [proj / "project.json", proj / "README.md"],
              note=f"{FORM_NAME.get(frm, frm)} · {fmt}")
    # 立项同时产出了分镜骨架（分镜表.md 就是骨架表），一并记上——
    # 否则 progress 会把"骨架已存在但没记录"显示成未做，让人以为还要再跑一次。
    skel_out = [proj / ("第01集-分镜表.md" if fmt == "series" else "分镜表.md")]
    PG.record(proj, "skel", skel_out, note="随立项生成")

    print(C.bold(f"\n  已建立：{proj}"))
    print(C.dim(f"  形态 {FORM_NAME.get(frm, frm)} · 规模 {fmt} · 题材 {args.genre}"))
    for f in sorted(proj.iterdir()):
        if f.is_file():
            print(f"    {f.name}")
    print(C.dim("\n  下一步：填 README 的一句话 → 过 plan 看节拍 → 填分镜表 → scan 质检"))
    return 0



# ------------------------------------------------------------
# plan
# ------------------------------------------------------------

def _proj_meta(proj: Path) -> dict:
    """读项目元数据（形态 / 规模），供 plan / skel 在没有显式参数时兜底。

    没有这一步，动漫形态的项目会拿 live_action 的基线算节拍——
    镜数区间与核心镜长全是错的，而且**不报错**（最坏的一类错）。
    """
    f = proj / "project.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


# ------------------------------------------------------------
# plan
# ------------------------------------------------------------

def cmd_plan(args):
    import plan as PL
    proj = resolve_project(args.project)
    meta = _proj_meta(proj)
    const = constants_for(proj, args.mode, form=args.form or meta.get("form"))
    fmt = args.format or meta.get("format") or "single"
    if fmt == "series":
        print(PL.render_episodes(args.episodes, const))
    else:
        print(PL.render_beats(const, args.total, platform=args.platform))
    return 0


# ------------------------------------------------------------
# skel
# ------------------------------------------------------------

def cmd_skel(args):
    import progress as PG
    import shotlist as SL
    proj = resolve_project(args.project)
    meta = _proj_meta(proj)
    const = constants_for(proj, args.mode, form=args.form or meta.get("form"))
    rows = SL.skeleton(const, args.total)
    md = SL.render_md(rows, proj.name, const=const)
    out = proj / "分镜表.md"
    if not args.write:
        print(md)
        print(C.dim(f"\n  干跑：以上是骨架内容，未写盘。加 --write 落到 {out.name}。"))
        return 0
    if args.dry_run:
        _write_plan({out: md + "\n"}, proj, True)
        return 0
    _write_plan({out: md + "\n"}, proj, False)
    PG.record(proj, "skel", [out], note=f"{len(rows)} 镜")
    print(C.ok(f"  已写入 {out}（{len(rows)} 镜）"))
    return 0



# ------------------------------------------------------------
# scan / check
# ------------------------------------------------------------

def cmd_scan(args):
    import scan as SC
    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"找不到分镜表：{p}")
    text = p.read_text(encoding="utf-8")
    shots = SC.parse_shotlist(text)
    if not shots:
        raise SystemExit("未解析到任何镜头——检查表头是否为 镜/时长/景别/谁在画面/动作/台词")

    proj = Path(args.project) if args.project else SC.find_project_root(p)
    # 形态兜底：显式 --form > 项目 project.json 的 form > 全局
    frm = args.form or (_proj_meta(proj).get("form") if proj else None)
    const = constants_for(proj, args.mode, form=frm) if proj \
        else constants_for(None, args.mode, form=frm)
    fmt = args.format or "single"
    # 档位与 is_ai 必须同源：只认 --live-shoot 不看解析出的档位，
    # 项目里写了 shot_profile = "live" 也会被 AI 后端 8s 上限拦下。
    mode = str(args.mode or const["drama"].get("shot_profile", "ai"))
    is_ai = False if args.live_shoot else (mode != "live")

    if args.changed:
        changed = parse_changed(args.changed)
        res = SC.analyze_changed(shots, changed, fmt=fmt, text=text, const=const,
                                 mode=mode, model=args.model, is_ai=is_ai)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else SC.render_changed(res, p.parent.name))
        if args.record:
            # 增量扫只重检了被改的镜头，全表比例判据是 stale 的——
            # 记成"质检完成"就是撒谎，宁可明说这次不记。
            print(C.warn("  增量重扫不记进度（全表判据未复算，记了等于撒谎）"))
        return 0

    res = SC.analyze(shots, fmt=fmt, text=text, const=const, mode=mode,
                     platform=args.platform, model=args.model, is_ai=is_ai)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
          else SC.render(res, p.parent.name, const=const))
    if args.record and proj:
        import progress as PG
        PG.record(proj, "scan", [p],
                  note=f"{len(res['blocking'])} 阻断 / {len(res['warning'])} 警告")
    return 0 if not res["blocking"] else 1


def cmd_progress(args):
    import progress as PG
    proj = resolve_project(args.project)
    rows = PG.status(proj)
    hint = PG.next_step(proj, rows)
    print(json.dumps({"stages": rows, "next": hint}, ensure_ascii=False, indent=2)
          if args.json else PG.render(rows, proj.name, hint))
    return 0


def cmd_check(args):
    import shotlist as SL
    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"找不到文件：{p}")
    proj = find_project_root(p)
    frm = args.form or (_proj_meta(proj).get("form") if proj else None)
    const = constants_for(proj, form=frm)
    # 和 scan 同一条规矩：is_ai 跟着解析出的档位走。
    # 真人项目被要求"AIGC 标识"是尺子用反了——它本来就不是 AI 生成的。
    mode = str(const["drama"].get("shot_profile", "ai"))
    is_ai = False if args.live_shoot else (mode != "live")
    res = SL.check_delivery(p.read_text(encoding="utf-8"), const,
                            form=frm, is_ai=is_ai)
    print(SL.render_check(res, p.stem))
    return 0 if res["ok"] else 1


# ------------------------------------------------------------
# continuity / forms / spec
# ------------------------------------------------------------

def cmd_continuity(args):
    import continuity as CY
    proj = resolve_project(args.project)
    res = CY.check(proj)
    print(json.dumps({k: v for k, v in res.items() if k != "ledger"},
                     ensure_ascii=False, indent=2) if args.json
          else CY.render(res, proj.name))
    return 0 if not res["blocking"] else 1


def cmd_forms(args):
    d = CONST["drama"]
    print(C.bold("\n  短剧工坊 · 形态"))
    print("  " + "─" * 56)
    for f in FORMS:
        blk = d.get(f) or {}
        s = (blk.get("shot") or {}) if blk else {}
        q = (blk.get("quality") or {}) if blk else {}
        has_panel = bool(blk.get("panel"))
        note = blk.get("note", "全局基线（真人/AI 写实视频）")
        core = (f"{s['core_sec_min']:g}-{s['core_sec_max']:g}s"
                if s.get("core_sec_min") is not None else "3-4s")
        fixed = (f"{q['fixed_ratio_min'] * 100:.0f}%"
                 if q.get("fixed_ratio_min") is not None else "65%")
        print(f"\n  {C.bold(FORM_NAME.get(f, f))}  {C.dim(f)}")
        print(f"    {note}")
        print(f"    核心镜长 {core}   固定镜下限 {fixed}   有格 {'是' if has_panel else '否'}")
    print()
    print(C.dim("  形态与档位正交：档位（ai/live）只管镜头语言，形态管媒介。"))
    print()
    return 0


def cmd_spec(args):
    const = constants_for(None)
    spec = platform_for(const, args.platform)
    m = model_for(const, args.model)
    print(C.bold("\n  短剧工坊 · 规格"))
    print("  " + "─" * 56)
    print(f"  平台   {spec.get('note', '')}")
    print(f"    画幅 {spec.get('aspect', '')}  {spec.get('width', '')}×{spec.get('height', '')}")
    band = spec.get("duration_band") or []
    if band:
        print(f"    时长档 {band[0]:.0f}-{band[1]:.0f}s")
    print(f"    安全区 上 {spec.get('safe_top_pct', 0) * 100:.0f}% "
          f"下 {spec.get('safe_bottom_pct', 0) * 100:.0f}%"
          f"  字幕入安全区 {'是' if spec.get('subtitle_safe') else '否'}")
    print(f"  后端   {m.get('note', '')}")
    print(f"    单镜上限 {m.get('max_shot_sec', '?')}s   提示词 ≤{m.get('max_prompt_chars', '?')} 字"
          f"   参考图 ≤{m.get('ref_images_max', '?')} 张   重试 ≤{m.get('max_retry', '?')} 次")
    flags = []
    if m.get("supports_multi_shot"):
        flags.append("多镜头叙事")
    if m.get("supports_audio"):
        flags.append("音频")
    if m.get("supports_keyframe"):
        flags.append("首尾帧")
    if flags:
        print(f"    支持 {'、'.join(flags)}")
    print()
    print(C.dim("  模型能力会升级，投产前必须重新核验——别把它当永久事实。"))
    print()
    return 0


# ------------------------------------------------------------
# quality（故事质量评分卡）
# ------------------------------------------------------------

def cmd_quality(args):
    import quality as QL
    import scan as SC
    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"找不到分镜表：{p}")
    text = p.read_text(encoding="utf-8")
    shots = SC.parse_shotlist(text)
    if not shots:
        raise SystemExit("未解析到任何镜头——检查表头是否为 镜/时长/景别/谁在画面/动作/台词")
    proj = Path(args.project) if args.project else SC.find_project_root(p)
    frm = args.form or (_proj_meta(proj).get("form") if proj else None)
    const = constants_for(proj, args.mode, form=frm) if proj \
        else constants_for(None, args.mode, form=frm)
    res = QL.score(shots, text, const["drama"])
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
          else QL.render(res, p.stem))
    return 0


# ------------------------------------------------------------
# selftest / doctor
# ------------------------------------------------------------

def cmd_selftest(args):
    import selftest as ST
    return ST.run()


def cmd_doctor(args):
    import platform
    d = CONST["drama"]
    print(C.bold("\n  短剧工坊 · 环境自检"))
    print("  " + "─" * 56)
    print(f"  Python      {platform.python_version()}")
    ok = sys.version_info >= (3, 11)
    print(f"  版本要求    >=3.11  {C.ok('✓') if ok else C.err('✗ tomllib 不可用')}")
    print(f"  项目根      {PROJECTS_DIR.parent}")
    print(f"  配置        {CONST['drama']['meta']['name']} v{d['meta']['version']}")
    print(f"  形态        {len(FORMS)} 种（{'、'.join(FORM_NAME.get(f, f) for f in FORMS)}）")
    print(f"  单集目标    {d['single']['target_duration_s']}s / {d['single']['shots_min']}-{d['single']['shots_max']} 镜")
    print(f"  系列默认    {d['series']['episodes_default']} 集 × {d['series']['episode_target_s']}s")
    n = len(list(PROJECTS_DIR.glob("*/"))) if PROJECTS_DIR.exists() else 0
    print(f"  项目数      {n}")
    print()
    return 0 if ok else 1



# ------------------------------------------------------------
# 参数
# ------------------------------------------------------------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="dramakit", description="短剧工坊 · 短剧 / 动漫短剧 / 漫剧的剧本与分镜产出系统",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("new", help="新建项目骨架")
    s.add_argument("project"); s.add_argument("genre")
    s.add_argument("--format", choices=["single", "series"])
    s.add_argument("--form", choices=list(FORMS), help="形态（默认 live_action）")
    s.add_argument("--episodes", type=int)
    s.add_argument("--force", action="store_true", help="目标已存在时覆盖")
    s.add_argument("--dry-run", action="store_true",
                   help="只打印将写入的文件清单，不落盘")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("plan", help="看节拍表 / 分集表")
    s.add_argument("project")
    s.add_argument("--episode", action="store_true", help="系列形态（等同 --format series）")
    s.add_argument("--format", choices=["single", "series"])
    s.add_argument("--episodes", type=int)
    s.add_argument("--total", type=float, help="覆盖单集总时长（秒）")
    s.add_argument("--mode", choices=["ai", "live"])
    s.add_argument("--form", choices=list(FORMS))
    s.add_argument("--platform", help="按平台规格核对时长档")
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("skel", help="生成分镜骨架")
    s.add_argument("project")
    s.add_argument("--write", action="store_true", help="写入 分镜表.md")
    s.add_argument("--dry-run", action="store_true",
                   help="与 --write 同用时只打印写入清单，不落盘")
    s.add_argument("--total", type=float)
    s.add_argument("--mode", choices=["ai", "live"])
    s.add_argument("--form", choices=list(FORMS))
    s.set_defaults(fn=cmd_skel)

    s = sub.add_parser("scan", help="分镜表质检")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.add_argument("--format", choices=["single", "series"])
    s.add_argument("--mode", choices=["ai", "live"], help="镜头语言档位")
    s.add_argument("--form", choices=list(FORMS), help="形态")
    s.add_argument("--platform", help="平台规格名（核对时长档）")
    s.add_argument("--model", help="生成后端名（核对单镜时长/参考图额度）")
    s.add_argument("--live-shoot", action="store_true", help="标记真人实拍（不查 AIGC 标识）")
    s.add_argument("--changed", help="增量重扫：3,5 或 3-7")
    s.add_argument("--project", help="显式指定项目根")
    s.add_argument("--record", action="store_true",
                   help="把本次结论与分镜表指纹记进 .progress.json")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("check", help="交付格式校验")
    s.add_argument("path")
    s.add_argument("--form", choices=list(FORMS))
    s.add_argument("--live-shoot", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("continuity", help="跨集一致性对账（连载专用）")
    s.add_argument("project")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_continuity)

    sub.add_parser("forms", help="列出所有形态与判据差异").set_defaults(fn=cmd_forms)

    s = sub.add_parser("spec", help="查看平台 / 后端规格")
    s.add_argument("--platform")
    s.add_argument("--model")
    s.set_defaults(fn=cmd_spec)

    s = sub.add_parser("quality", help="故事质量评分卡（结构特征参考分，不拦稿）")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.add_argument("--mode", choices=["ai", "live"])
    s.add_argument("--form", choices=list(FORMS))
    s.add_argument("--project", help="显式指定项目根")
    s.set_defaults(fn=cmd_quality)

    s = sub.add_parser("progress", help="看项目做到哪一步（断点续跑）")
    s.add_argument("project")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_progress)

    sub.add_parser("selftest", help="回归自检").set_defaults(fn=cmd_selftest)
    sub.add_parser("doctor", help="环境自检").set_defaults(fn=cmd_doctor)
    return ap



def main():
    ensure_utf8()
    ap = build_parser()
    args = ap.parse_args()
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
