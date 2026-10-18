# 短剧工坊 · 短剧 / 漫剧的剧本与分镜产出系统

一句话：**结构先行，镜头后配，格式定死。**

把"一句话灵感 → 节拍 → 分镜表 → 质检 → 定稿"做成一条可执行、可校验的链路。
纯 Python 标准库，零第三方依赖，不调任何模型——**它不写剧本，它管住剧本的形状**。

## 它解决什么问题

短剧拍砸，八成不是台词写得差，是结构没搭对：

- 前 3 秒没把人钉住，观众划走了
- 四段配比失衡，压力段太短，反转没有铺垫
- 镜数与时长对不上，剪的时候才发现少了 20 秒
- 长镜是空的，演员在画面里没东西可演
- 交付时才发现少了"验收额度"，模型抽到第 8 条还在抽

这些都不是审美问题，是**可以机械检查的**问题。本项目只做机械检查。

## 能做什么

| 命令 | 作用 |
|---|---|
| `dramakit new` | 建项目骨架（README / 节拍表 / 分镜表 / 分集表） |
| `dramakit plan` | 按配比算出四段时长，或排出系列分集表与卡点位置 |
| `dramakit skel` | 按节拍与核心镜长，算出每段该给几镜，排出空表 |
| `dramakit scan` | 分镜表质检：时长 / 镜数 / 镜头语言 / 钩子密度 / 对白占比 |
| `dramakit check` | 交付格式校验：五块结构、六列表头、验收额度 |
| `dramakit selftest` | 36 项回归断言，秒级，不耗 token |

## 不能做什么

- 不生成台词、不生成画面、不抽提示词
- 不管图生视频、TTS、剪辑——这些交外部工具
- 不判断"这个题材行不行"——**题材不行，剧本怎么改都是白改**，那是人的判断

## 快速开始

```bash
cd 短剧工坊

# 环境自检
python engine/dramakit.py doctor

# 立项（单集完结）
python engine/dramakit.py new 我的剧 "一句话灵感"

# 看四段节拍
python engine/dramakit.py plan 我的剧

# 生成分镜骨架并写入
python engine/dramakit.py skel 我的剧 --write

# 填完动作和台词后质检
python engine/dramakit.py scan 我的剧/分镜表.md

# 定稿前校验交付格式
python engine/dramakit.py check 我的剧/我的剧.md
```

系列连载：

```bash
python engine/dramakit.py new 我的系列 "一句话灵感" --format series --episodes 60
python engine/dramakit.py plan 我的系列 --episode
```

## 两种镜头语言档位

**判据方向是反的，不能共用一把尺子。**

| | AI 生成档 | 真人实拍档 |
|---|---|---|
| 单镜上限 | 8 秒（再长画面崩、动作漂） | 10 秒（没有这个约束） |
| 核心镜长 | 3-4 秒 | 5-10 秒 |
| 核心镜占比 | ≥55% | ≥40% |
| 开场窗口 | 3 秒 | 6 秒 |
| 特写反应镜 | ≤3 秒 | ≤6 秒（演员要时间建立情绪） |

压缩镜头的正确做法不是"每镜都拉长"，而是
**中景长镜承担连续段落，只给信息点留独立短镜**。

切换档位：

```bash
python engine/dramakit.py scan 我的剧/分镜表.md --mode live
```

或在项目 `config/overrides.toml` 里写 `shot_profile = "live"`，之后自动生效。

## 目录

```
短剧工坊/
├── engine/
│   ├── dramakit.py        # 命令行入口
│   └── lib/
│       ├── common.py      # 常数加载 / 档位解析 / 终端着色
│       ├── plan.py        # 节拍与分集表
│       ├── shotlist.py    # 分镜骨架 + 交付校验
│       ├── scan.py        # 分镜表质检
│       └── selftest.py    # 回归自检
├── config/dramakit.toml   # 唯一常数表，代码里不写死数字
├── references/            # 结构 / 镜头 / 交付 三份规范
├── projects/              # 一个项目一个目录
│   └── 示例-十七楼/        # 9 镜 61 秒，可直接跑
└── _test/
```

## 判据在哪

全部在 `config/dramakit.toml`。代码里没有硬编码阈值——改一个数，全套判据跟着变。
项目级差异写 `<项目>/config/overrides.toml`，深覆盖全局值，
优先级 **项目覆盖 > 档位 > 全局基线**。

## 版权

MIT。代码与文档均为独立实现，不含任何第三方项目的源代码或文档片段。
