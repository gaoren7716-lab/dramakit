"""代码指纹比对：检查 qianmian 是否与 _sources 下的开源仓库存在逐字重合。

只做一件事——把本项目每个文件的"实质行"（去掉注释、空行、过短行）拿去
在 _sources 的所有源码里搜索，命中即打印。用于回答"有没有抄代码"。
"""
import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "小说" / "_sources"   # 原料库在小说/ 下，不在本仓库旁
TARGET = ROOT  # qianmian

EXTS = {".py", ".md", ".toml", ".html", ".json", ".js", ".css"}
SKIP_DIRS = {"_sources", ".git", "__pycache__", "_test", "projects", "dist", "build"}
MIN_LEN = 40

# 编程通用惯用法：这类行在任何 Python 项目里都会出现，重合不构成抄袭证据。
# 不排除掉会把信噪比压到零——第一次跑 257 处命中全是 import 和 `if __name__`。
BOILER = re.compile(
    r"""^(
        (from\s+\S+\s+)?import\s | from\s+\S+\s+import\s
      | if\s+__name__\s*==\s*['"]__main__['"]
      | (try|except|finally|else|elif|return|yield|raise|pass|break|continue)\b
      | sys\.path\.insert
      | \w+\s*=\s*(f?['"]).*?\2$
      | \w+\.(read_text|write_text|exists|mkdir|glob|iterdir|open)\(
      | for\s+\w+(\s*,\s*\w+)?\s+in\s+enumerate\(
      | (with\s+open|print\(|logging\.|logger\.)
      | (def|class)\s+\w+\(.*\)\s*(->.*)?:$
      | @\w+
      | [\]\}\)]+[,:]?$
    )""",
    re.X,
)


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s).strip()


def substantive(line: str) -> bool:
    s = line.strip()
    if len(s) < MIN_LEN:
        return False
    if s.startswith(("#", "//", "*", "/*", "<!--")):
        return False
    # 纯符号行（分隔线、括号）不算
    if not re.search(r"[\w\u4e00-\u9fff]", s):
        return False
    if BOILER.match(s):
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
    hits, n_files, n_lines = [], 0, 0
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
            n_files += 1
            for i, line in enumerate(txt.splitlines(), 1):
                if not substantive(line):
                    continue
                n_lines += 1
                h = hashlib.sha1(norm(line).encode()).hexdigest()
                if h in idx:
                    hits.append((p.relative_to(TARGET), i, line.strip()[:80], idx[h][0]))
    return hits, n_files, n_lines


def main():
    print(f"建立指纹库：{SRC}")
    idx, n = build_index()
    print(f"  索引 {n} 个源文件，{len(idx)} 条实质行\n")
    hits, nf, nl = scan_ours(idx)
    print(f"比对 {nf} 个本项目文件，{nl} 条实质行")
    print(f"逐字重合：{len(hits)} 处\n")
    for ours, ln, text, other in hits[:60]:
        print(f"  {ours}:{ln}  {text}")
        print(f"      ↳ 命中 {other.relative_to(SRC)}")
    if not hits:
        print("  无。本项目与 _sources 不存在逐字重合的实质行。")
    return 0 if not hits else 1


if __name__ == "__main__":
    raise SystemExit(main())
