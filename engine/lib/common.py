# -*- coding: utf-8 -*-
"""短剧工坊 · 公共层

常数加载 / 档位解析 / 文本度量。纯标准库，零第三方依赖。
Python 3.11+（用到 tomllib）。
"""
from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent   # engine/lib/common.py → 项目根
CONFIG_FILE = ROOT / "config" / "dramakit.toml"
PROJECTS_DIR = ROOT / "projects"


# ------------------------------------------------------------
# 常数
# ------------------------------------------------------------

def _load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"找不到配置：{path}")
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def deep_merge(base: dict, over: dict) -> dict:
    """递归合并：over 覆盖 base，字典逐层合、列表整体替换。"""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# 对外只暴露 CONST["drama"] 这一层——扫描模块按 `CONST["drama"]["shot"]` 取表，
# 配置文件本身不必再套一层 `[drama]`，读起来更干净。
RAW = _load(CONFIG_FILE)
CONST = {"drama": RAW}


def apply_shot_profile(const: dict, profile: str = "ai") -> dict:
    """按镜头语言档位生成常数副本。

    `[shot] / [single] / [quality]` 是 **AI 生成档**的基线。
    `[live.*]` 只写与 AI 档的差异项，按表名分组逐表覆盖。

    为什么要分档：**两档判据方向是反的**。AI 生成单镜 3-4 秒最稳（再长画面崩、
    动作漂），真人实拍相反——长镜省机位、表演连贯，一个中景长镜能把整段对话
    一次演完。用同一把尺子量两种形态，必然一边被误判。
    """
    out = deep_merge(const, {})
    d = out["drama"]
    if profile == "live":
        live = d.get("live") or {}
        patch = {}
        for table in ("shot", "single", "quality"):
            if isinstance(live.get(table), dict) and live[table]:
                patch[table] = live[table]
        d = deep_merge(d, patch)
        out["drama"] = d
    d["_profile"] = profile if profile in ("ai", "live") else "ai"
    return out


def constants_for(proj=None, profile: str | None = None) -> dict:
    """项目级常数 = 全局 + 档位 + <项目>/config/overrides.toml。

    顺序不能反：**项目覆盖永远最高**。档位只是"这类形态的默认区间"，
    作品自己的特殊约定（这部长镜更多、允许 12 秒）必须压得住档位。
    """
    over = {}
    if proj is not None:
        f = Path(proj) / "config" / "overrides.toml"
        if f.exists():
            try:
                with open(f, "rb") as fh:
                    over = tomllib.load(fh)
            except Exception:
                over = {}

    prof = (profile
            or over.get("shot_profile")
            or (over.get("drama") or {}).get("shot_profile")
            or RAW.get("shot_profile") or "ai")
    # overrides 可能直接写 [shot] / [single]，也可能沿用 [drama.*]，两种都认
    base = apply_shot_profile(CONST, prof)
    flat = {k: v for k, v in over.items() if k != "drama"}
    nested = over.get("drama") or {}
    out = deep_merge(base, {"drama": deep_merge(flat, nested)}) if over else base
    out["_has_overrides"] = bool(over)
    return out


def find_project_root(path, levels: int = 4):
    """从分镜表所在目录向上找项目根（含 config/overrides.toml 或 project.json）。"""
    p = Path(path).resolve()
    if p.is_file():
        p = p.parent
    for _ in range(levels + 1):
        if (p / "config" / "overrides.toml").exists() or (p / "project.json").exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return None


# ------------------------------------------------------------
# 终端输出
# ------------------------------------------------------------

class C:
    """ANSI 着色。不支持颜色时自动降级为空串。"""

    _on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    @staticmethod
    def _w(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if C._on else s

    @staticmethod
    def bold(s): return C._w("1", s)

    @staticmethod
    def dim(s): return C._w("2", s)

    @staticmethod
    def ok(s): return C._w("32", s)

    @staticmethod
    def warn(s): return C._w("33", s)

    @staticmethod
    def err(s): return C._w("31", s)

    @staticmethod
    def cyan(s): return C._w("36", s)


def bar(v: float, mx: float, width: int = 14) -> str:
    """字符条形图。mx 为 0 时返回空条，不除零。"""
    if not mx:
        return "·" * width
    n = max(0, min(width, round(v / mx * width)))
    return "█" * n + "·" * (width - n)


def ensure_utf8():
    """Windows 控制台默认 GBK，输出中文会炸。双击 exe / 管道输出都要这一下。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
