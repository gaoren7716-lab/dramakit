"""中文短句查重：补充长行探针（≥40 字）会漏掉的那一半。

**中文资料的重合单位常常是 15-25 字的表格行与清单项**，卡 40 字门槛会直接放过。
本项目的方法论文档与 _sources 里的中文 Skill 文档最容易在"表格行"这一层撞车。

所以两套阈值必须一起跑：
  - 长行探针 `plagiarism_probe.py`：≥40 字的实质行
  - 本探针：≥18 字的中文行（含表格行、清单项）

去掉标点后比对，避免因标点差异漏判。
"""
import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "小说" / "_sources"
TARGET = ROOT

EXTS = {".py", ".md", ".toml"}
SKIP_DIRS = {"_sources", ".git", "__pycache__", "_test", "projects", "dist", "build"}
MIN_CJK = 18   # 去掉标点后至少这么多汉字

# 标点集合用列表拼，避免全角引号把正则字符串截断
_PUNCT_CHARS = list("，。！？、；：（）《》〈〉「」『』【】…—～·,.!?;:()[]{}<>*#-=+/\\|`'\" \t")
_PUNCT_RE = "[" + re.escape("".join(_PUNCT_CHARS)) + "]+"
PUNCT = re.compile(_PUNCT_RE)


def cjk_len(s: str) -> int:
    return len(PUNCT.sub("", s))


def norm(s: str) -> str:
    return PUNCT.sub("", s)

# 通用惯用法与结构性文本：这类行在任何中文文档里都会出现，重合不构成抄袭证据。
# ⚠️ 别用 re.X 写整块备选——空白被忽略后 `-{3,}$` 之类的分支会误命中普通中文行
# （实测：一条正常中文句被判成 boiler）。改用直白的前缀判断。
_HEADER_RE = re.compile(r"^#{1,6}\s*(?:[一二三四五六七八九十]+|\d+)?\s*[、.．]?\s*$")
_TABLE_SEP_RE = re.compile(r"^\|[\s\-:|]+\|$")
_SEP_RE = re.compile(r"^[-=]{3,}$")
_TABLE_HEAD_RE = re.compile(r"^\|\s*(ID|id|镜|集|人物|场景|时长|景别|状态|说明|序号|编号|段|项)\s*\|")


def substantive(line: str) -> bool:
    s = line.strip()
    if not s or s.startswith(("<!--", "```")):
        return False
    if (_HEADER_RE.match(s) or _TABLE_SEP_RE.match(s)
            or _SEP_RE.match(s) or _TABLE_HEAD_RE.match(s)):
        return False
    # 主门槛：去标点后总长 ≥ MIN_CJK（数字与英文都算，因为"第 20 集"这类是中文行）
    if cjk_len(s) < MIN_CJK:
        return False
    # 下限：至少得有 8 个汉字，避免纯数字/时间戳行进来
    if len(re.findall(r"[\u4e00-\u9fff]", s)) < 8:
        return False
    return True


def build_index():
    idx = {}
    n_files = 0
    for dp, dns, fns in os.walk(SRC):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix.lower() not in EXTS:
                continue
            try:
                if p.stat().st_size > 2_000_000:
                    continue
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            n_files += 1
            for line in txt.splitlines():
                if not substantive(line):
                    continue
                h = hashlib.sha1(norm(line).encode()).hexdigest()
                idx.setdefault(h, (p, line.strip()))
    return idx, n_files


def scan_ours(idx):
    hits, nf, nl = [], 0, 0
    for dp, dns, fns in os.walk(TARGET):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix.lower() not in EXTS:
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            nf += 1
            for i, line in enumerate(txt.splitlines(), 1):
                if not substantive(line):
                    continue
                nl += 1
                h = hashlib.sha1(norm(line).encode()).hexdigest()
                if h in idx:
                    hits.append((p.relative_to(TARGET), i, line.strip()[:90], idx[h][0]))
    return hits, nf, nl


def main():
    print(f"建立中文短句指纹库（≥{MIN_CJK} 字）：{SRC}")
    idx, n = build_index()
    print(f"  索引 {n} 个源文件，{len(idx)} 条中文短句\n")
    hits, nf, nl = scan_ours(idx)
    print(f"比对 {nf} 个本项目文件，{nl} 条中文短句")
    print(f"逐字重合：{len(hits)} 处\n")
    for ours, ln, text, other in hits[:60]:
        print(f"  {ours}:{ln}  {text}")
        print(f"      ↳ 命中 {other.relative_to(SRC)}")
    if not hits:
        print("  无。本项目与 _sources 不存在逐字重合的中文短句。")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
