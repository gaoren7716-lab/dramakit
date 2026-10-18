#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""短剧工坊 · 命令行入口

    python engine/dramakit.py new  我的剧 "一句话灵感" [--format single|series]
    python engine/dramakit.py plan 我的剧 [--episode]
    python engine/dramakit.py skel 我的剧 [--write]
    python engine/dramakit.py scan 我的剧/分镜表.md [--mode ai|live] [--json]
    python engine/dramakit.py check 我的剧/我的剧.md
    python engine/dramakit.py selftest
    python engine/dramakit.py doctor
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from common import C, CONST, PROJECTS_DIR, constants_for, ensure_utf8, find_project_root  # noqa: E402


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

def cmd_new(args):
    import plan as PL
    import shotlist as SL

    name = args.project
    fmt = args.format or "single"
    proj = PROJECTS_DIR / name
    if proj.exists() and not args.force:
        raise SystemExit(f"项目已存在：{proj}（要重建加 --force）")
    (proj / "config").mkdir(parents=True, exist_ok=True)

    meta = {"name": name, "genre": args.genre, "format": fmt,
            "version": CONST["drama"]["meta"]["version"]}
    (proj / "project.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (proj / "README.md").write_text(
        SL.project_readme(name, args.genre, fmt), encoding="utf-8")

    if fmt == "series":
        n = args.episodes or CONST["drama"]["series"]["episodes_default"]
        (proj / "分集表.md").write_text(PL.episodes_md(n) + "\n", encoding="utf-8")
        (proj / "第01集-分镜表.md").write_text(
            SL.render_md(SL.skeleton()) + "\n", encoding="utf-8")
    else:
        (proj / "节拍表.md").write_text(PL.beats_md() + "\n", encoding="utf-8")
        (proj / "分镜表.md").write_text(SL.render_md(SL.skeleton()) + "\n", encoding="utf-8")

    print(C.bold(f"\n  已建立：{proj}"))
    print(C.dim(f"  形态 {fmt} · 题材 {args.genre}"))
    for f in sorted(proj.iterdir()):
        print(f"    {f.name}")
    print(C.dim("\n  下一步：填 README 的一句话 → 过 plan 看节拍 → 填分镜表 → scan 质检"))
    return 0


# ------------------------------------------------------------
# plan
# ------------------------------------------------------------

def cmd_plan(args):
    import plan as PL
    proj = resolve_project(args.project)
    const = constants_for(proj, args.mode)
    fmt = args.format
    if not fmt:
        meta = proj / "project.json"
        fmt = json.loads(meta.read_text(encoding="utf-8")).get("format", "single") if meta.exists() else "single"
    if fmt == "series":
        print(PL.render_episodes(args.episodes, const))
    else:
        print(PL.render_beats(const, args.total))
    return 0


# ------------------------------------------------------------
# skel
# ------------------------------------------------------------

def cmd_skel(args):
    import shotlist as SL
    proj = resolve_project(args.project)
    const = constants_for(proj, args.mode)
    rows = SL.skeleton(const, args.total)
    md = SL.render_md(rows, proj.name)
    if args.write:
        out = proj / "分镜表.md"
        out.write_text(md + "\n", encoding="utf-8")
        print(C.ok(f"  已写入 {out}（{len(rows)} 镜）"))
    else:
        print(md)
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
    const = constants_for(proj, args.mode) if proj else constants_for(None, args.mode)
    fmt = args.format or "single"

    if args.changed:
        changed = parse_changed(args.changed)
        res = SC.analyze_changed(shots, changed, fmt=fmt, text=text, const=const)
        print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
              else SC.render_changed(res, p.parent.name))
        return 0

    res = SC.analyze(shots, fmt=fmt, text=text, const=const)
    print(json.dumps(res, ensure_ascii=False, indent=2) if args.json
          else SC.render(res, p.parent.name, const=const))
    return 0 if not res["blocking"] else 1


def cmd_check(args):
    import shotlist as SL
    p = Path(args.path)
    if not p.exists():
        raise SystemExit(f"找不到文件：{p}")
    res = SL.check_delivery(p.read_text(encoding="utf-8"), constants_for(find_project_root(p)))
    print(SL.render_check(res, p.stem))
    return 0 if res["ok"] else 1


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
        prog="dramakit", description="短剧工坊 · 短剧 / 漫剧的剧本与分镜产出系统",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("new", help="新建项目骨架")
    s.add_argument("project"); s.add_argument("genre")
    s.add_argument("--format", choices=["single", "series"])
    s.add_argument("--episodes", type=int)
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_new)

    s = sub.add_parser("plan", help="看节拍表 / 分集表")
    s.add_argument("project")
    s.add_argument("--episode", action="store_true", help="系列形态（等同 --format series）")
    s.add_argument("--format", choices=["single", "series"])
    s.add_argument("--episodes", type=int)
    s.add_argument("--total", type=float, help="覆盖单集总时长（秒）")
    s.add_argument("--mode", choices=["ai", "live"])
    s.set_defaults(fn=cmd_plan)

    s = sub.add_parser("skel", help="生成分镜骨架")
    s.add_argument("project")
    s.add_argument("--write", action="store_true", help="写入 分镜表.md")
    s.add_argument("--total", type=float)
    s.add_argument("--mode", choices=["ai", "live"])
    s.set_defaults(fn=cmd_skel)

    s = sub.add_parser("scan", help="分镜表质检")
    s.add_argument("path")
    s.add_argument("--json", action="store_true")
    s.add_argument("--format", choices=["single", "series"])
    s.add_argument("--mode", choices=["ai", "live"], help="镜头语言档位")
    s.add_argument("--changed", help="增量重扫：3,5 或 3-7")
    s.add_argument("--project", help="显式指定项目根")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("check", help="交付格式校验")
    s.add_argument("path")
    s.set_defaults(fn=cmd_check)

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
