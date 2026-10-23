# -*- coding: utf-8 -*-
"""短剧工坊 · 公共层

常数加载 / 档位解析 / 文本度量。纯标准库，零第三方依赖。
Python 3.11+（用到 tomllib）。
"""
from __future__ import annotations

import os
import re
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


FORMS = ("live_action", "anime", "motion_comic", "static_comic")
PROFILES = ("ai", "live")

# 形态别名：旧项目写 comic，等同 motion_comic。保留是为了不让既有项目失效。
FORM_ALIAS = {"comic": "motion_comic", "live": "live_action", "ai": "live_action"}

# 形态专属要替换的表。live_action 没有专属块，直接用全局基线。
# panel 是"格"（漫剧/静态漫独有），asset 是资产台账（动画/漫画独有），
# hook 是秒级锚点表（各形态爆破节奏不同，容差要跟着变）。
FORM_TABLES = ("shot", "single", "quality", "panel", "asset", "hook", "story",
               "work")

# 哪些形态有"格"（一组格完成一个起承转合）——只有两种漫。
# 动漫短剧是连续动画，用影视景别与视频交付，不算"格"形态。
PANEL_FORMS = ("motion_comic", "static_comic")
# 哪些形态是静态图（无连续视频）——决定了预算/重抽口径
FRAME_FORMS = ("motion_comic", "static_comic")


def normalize_form(form: str | None) -> str:
    """把形态名归一化：别名解析 + 未知值退回 live_action。"""
    f = str(form or "").strip()
    f = FORM_ALIAS.get(f, f)
    return f if f in FORMS else "live_action"


def normalize_profile(profile: str | None) -> str:
    p = str(profile or "").strip()
    return p if p in PROFILES else "ai"


def apply_form_profile(const: dict, form: str = "live_action",
                       profile: str = "ai") -> dict:
    """按「形态 + 档位」生成常数副本。

    两个维度是**正交**的，别混：
      - **形态（form）**：画面从哪来。live_action 真人/AI 写实视频、
        anime 动漫短剧（连续动画）、motion_comic 动态漫、static_comic 静态漫。
        换的是媒介——有没有连续视频、对白走配音还是走气泡、要不要资产台账。
      - **档位（profile）**：ai 生成 vs live 真人实拍。
        换的只是镜头语言（单镜多长最稳、长镜算不算省机位）。

    所以形态决定"用哪套表"，档位只在这套表上做微调。静态漫/动态漫
    与是不是真人无关（画面都是静态图），档位对它只影响 live 覆盖项里
    显式写了的那些键。

    优先级：项目覆盖 > 形态 > 档位 > 全局基线。
    """
    # ★ 必须先真正复制 drama 表。deep_merge(const, {}) 只复制顶层：
    # out["drama"] 与 const["drama"] 仍是同一个对象，写 form 会污染全局常数，
    # 导致上一次调用选的形态泄漏到下一次（实测：跑过一次 comic，之后默认形态变成 comic）。
    out = deep_merge(const, {})
    d = deep_merge(out["drama"], {})
    out["drama"] = d
    frm = normalize_form(form)
    prof = normalize_profile(profile)

    # 1) 形态：按 <form>.<table> 覆盖基线三表 + 形态专属表
    form_block = d.get(frm) or {}
    if frm != "live_action" and form_block:
        patch = {t: form_block[t] for t in FORM_TABLES
                 if isinstance(form_block.get(t), dict) and form_block[t]}
        d = deep_merge(d, patch)
    # 形态决定是否存在 panel / asset：没有就退回空字典而非删键，
    # 下游 len()/get() 不会炸。
    for t in ("panel", "asset"):
        if t not in d or not isinstance(d.get(t), dict):
            d.setdefault(t, {})

    # 2) 档位微调：只认 live.* 里按表名分组的差异项
    if prof == "live":
        live = d.get("live") or {}
        lp = {t: live[t] for t in ("shot", "single", "quality", "hook", "story")
              if isinstance(live.get(t), dict) and live[t]}
        if lp:
            d = deep_merge(d, lp)

    d["form"] = frm
    d["_profile"] = prof
    out["drama"] = d
    return out



def constants_for(proj=None, profile: str | None = None,
                  form: str | None = None) -> dict:
    """项目级常数 = 全局 + 形态 + 档位 + <项目>/config/overrides.toml。

    顺序不能反：**项目覆盖永远最高**。形态与档位只是"这类作品的默认区间"，
    作品自己的特殊约定（这部长镜更多、允许 12 秒）必须压得住它们。
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

    nested = over.get("drama") or {}
    flat = {k: v for k, v in over.items() if k != "drama"}
    merged_over = deep_merge(flat, nested)

    prof = normalize_profile(
        profile or merged_over.get("shot_profile")
        or RAW.get("shot_profile") or "ai")
    frm = normalize_form(
        form or merged_over.get("form")
        or RAW.get("form") or "live_action")

    base = apply_form_profile(CONST, frm, prof)
    out = deep_merge(base, {"drama": merged_over}) if merged_over else base
    out["_has_overrides"] = bool(over)
    out["_form"] = frm
    out["_profile"] = prof
    return out


# ------------------------------------------------------------
# 平台 / 模型 / 合规规格
# ------------------------------------------------------------

def platform_for(const: dict | None = None, name: str | None = None) -> dict:
    """取平台规格表（画幅 / 时长档 / 安全区）。

    优先级：显式 name > 项目/全局 `[platform]` 键 > `[platform.default]`。
    表里没有的键退回 default 的对应值，避免下游 KeyError。
    """
    d = (const or CONST)["drama"]
    sel = d.get("select") or {}
    key = str(name or sel.get("platform") or "default").strip()
    tbl = (d.get("platform") or {})
    spec = deep_merge(tbl.get("default") or {}, tbl.get(key) or {})
    if not spec:
        spec = {"aspect": "9:16", "duration_band": [30, 180]}
    return spec


def model_for(const: dict | None = None, name: str | None = None) -> dict:
    """取生成后端能力表（单镜时长上限 / 提示词字数 / 参考图额度）。

    **超限必须拒绝，不得截断后调上游**——本函数只负责给出上限，
    是否拒绝由调用方（scan.check_model_limits）判定。
    """
    d = (const or CONST)["drama"]
    sel = d.get("select") or {}
    key = str(name or sel.get("model") or "default").strip()
    tbl = (d.get("model") or {})
    return deep_merge(tbl.get("default") or {}, tbl.get(key) or {})


def compliance_for(const: dict | None = None) -> dict:
    """取合规表。合并 default 兜底，缺表时返回一份保守默认。"""
    d = (const or CONST)["drama"]
    base = {
        "aigc_label_required": True,
        "aigc_label_text": "本片由 AI 生成合成",
        "aigc_label_in_delivery": True,
        "minors_protection": True,
        "minors_terms": [],
        "sensitive_terms": [],
        "forbidden_terms": [],
    }
    return deep_merge(base, d.get("compliance") or {})


def sizes_for(const: dict | None = None, form: str | None = None) -> tuple:
    """取本形态的景别白名单。

    ★ 视频形态原先**没有白名单**，于是"荒谬景别"这种值能一路通过
      scan 与 check 发出去。景别是有限枚举不是自由文本——写错景别，
      模型抽出来的提示词就是错的，而且没人知道错在哪。
      漫剧用格名，白名单在各自的 [panel].sizes 里；这里只管视频形态。
    """
    d = (const or CONST)["drama"]
    frm = normalize_form(form or d.get("form") or "live_action")
    panel = d.get("panel") or {}
    if frm in PANEL_FORMS and panel.get("sizes"):
        return tuple(panel["sizes"])
    tbl = d.get("sizes") or {}
    spec = deep_merge(tbl.get("default") or {}, tbl.get(frm) or {})
    sizes = spec.get("sizes") or []
    return tuple(sizes) if sizes else ("大特写", "特写", "近景", "中景",
                                       "中全景", "全景", "远景")


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
# 表格行切分
# ------------------------------------------------------------
# ★ 全角竖线 ｜ 是**另一种写法**，不是半角 | 的别名。
#   老板的提交模板是这么写的：`镜号｜目标时长｜景别｜谁在画面｜…`
#   —— 没有外框竖线，分隔符是全角。只认半角会让整张表**解析成空列表**，
#   而且不报错：scan 拿到 0 个镜头，所有判据"全部通过"。
#   这是"按符号查表"的又一例：分隔符也是一种写法，写法不止一种。

# 列名后缀的括注是**给人看的填表说明**，不是列名的一部分。
# 老板模板的末列叫「台词原文（无则写无）」——不剥掉就认不出这一列，
# 整列台词被判成"缺失"，所有"有台词"的判据一起失效。
_PAREN = re.compile(r"[（(][^）)]*[）)]\s*$")


def split_row(line: str) -> list[str]:
    """切一行表格。**两种写法各用各的分隔符，不能混**：

    - Markdown 表格：行首有半角外框 `|`，按半角 `|` 切。
      单元格内部可以合法出现全角 `｜`（如「周宁｜推门」），**必须留着**。
    - 老板提交模板：没有外框竖线，整行按全角 `｜` 切。

    ★ 一刀切按"两种竖线都算分隔符"会切碎单元格：
      实测「周宁｜推门」被切成两格，整张表右移一列，
      台词列读到的是画面内容，而且**不报错**——错的解析比失败的解析更坏。
    """
    s = line.strip()
    if not s:
        return []
    if s.startswith("|"):                 # markdown 表格：外框半角
        return [c.strip() for c in s.strip("|").split("|")]
    if "｜" in s:                          # 老板模板：全角竖线，无外框
        return [c.strip() for c in s.strip("｜").split("｜")]
    return []


def is_row(line: str) -> bool:
    """这一行像不像表格行：半角 | 开头，或含全角 ｜。"""
    s = line.strip()
    return s.startswith("|") or "｜" in s


def norm_head(h: str) -> str:
    """把列名归一成短名：剥掉末尾括注。"""
    return _PAREN.sub("", (h or "").strip()).strip()


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
