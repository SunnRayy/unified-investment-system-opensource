[English](quickstart.md) | 简体中文

# 快速开始

克隆 → 生成演示数据 → 同步 → 看到一个填满数据的 Dashboard。下面每一条
命令都跑在一个合成的虚构角色（persona）数据上（见
[`tools/demo_data/persona.yaml`](../tools/demo_data/persona.yaml)），
由固定随机种子确定性生成——不需要任何人的真实财务数据。

已经在一份全新的 checkout 上完整跑通过；预计总耗时约 10 分钟，其中大部分
时间花在首次同步时抓取实时行情上。

## 1. 克隆并安装

需要 **Python 3.9–3.13**（已在 3.9.6 和 3.13.7 上测试过——`requirements.txt`
用环境标记（environment markers）为每个版本挑选经过验证的依赖版本）。
创建虚拟环境之前，先确认你的默认 `python3`：

```bash
python3 --version   # must be 3.9–3.13; if not, point at a specific interpreter,
                     # e.g. python3.12 -m venv .venv
```

```bash
git clone https://github.com/SunnRayy/huinsight
cd huinsight

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd ux-command-center && npm install && cd ..
```

## 2. 生成演示数据

```bash
.venv/bin/python tools/demo_data/generate.py
```

这条命令会在 `tools/demo_data/out/` 下写出 9 个合成的原始数据文件——
Schwab 的 CSV 持仓 + 交易记录、IBKR Flex 报告，以及境内基金、黄金、保险、
RSU 和财务汇总表这几张 Excel 工作簿。同样的种子，每次都产出同样的结果
（`tools/demo_data/generate.py --help` 可查看 `--out-dir` / `--persona`
覆盖参数）。

**在继续之前先检查一下**——如果上一条命令报错了（缺依赖、权限问题），
下面的复制步骤会报一个让人摸不着头脑的「no matches found」，而不是真正的
出错原因。所以先确认确实有 9 个文件：

```bash
find tools/demo_data/out -type f | wc -l   # expect 9
```

如果数字不是 9，重新运行一次生成器，并把它的输出看一遍再继续——不要在
一次没跑完整/失败的运行之后，直接进行下面的复制步骤。

把所有文件收进同一个平铺的文件夹——两个 IBKR 文件本来落在子目录里
（`out/ibkr/`、`out/ibkr_trades/`），这是因为这个数据源自身的单元测试
fixture 就是这样组织的；而真实的 `data_dir` 期望它们和其他文件一起平铺
放在同一层，这样同一个数据源才能挑出两者中较新的那份：

```bash
mkdir -p data/import
cp tools/demo_data/out/*.csv tools/demo_data/out/*.xlsx data/import/
cp tools/demo_data/out/ibkr/*.csv tools/demo_data/out/ibkr_trades/*.csv data/import/
```

（zsh 用户注意：如果这里出现一个没匹配上的通配符——比如上面的检查步骤被
跳过了、`out/` 其实是空的——zsh 会直接中断整段粘贴进去的命令块，报
「no matches found」，而不像 bash 那样只中断出问题的那一条命令。前面那个
检查步骤存在的意义，就是让你在这一步之前先撞上一个清楚的报错，而不是在
这一步撞上一个让人困惑的报错。）

## 3. 配置

```bash
cp config/settings.example.yaml config/settings.yaml
```

演示环境不需要改任何东西——示例配置里的 `finance_dir` 已经指向
`./data/import`，正好对上第 2 步的输出，而且每个数据源默认都是启用的
（对演示数据集来说这是对的，因为它包含全部 7 个数据源）。之后想切换到你
自己的真实数据，只需要改一行路径；见下方的「接下来做什么」。

## 4. 初始化数据库并同步

```bash
.venv/bin/python main.py --init
.venv/bin/python main.py --sync-v3
```

`--init` 会创建 `data/unified.duckdb` 并应用完整的 schema——如果目标路径
还不存在，运行这条命令是安全的，而这正是一次全新 clone 的正常情况。
`--sync-v3` 会读取全部 7 个演示数据源，跑完整条流水线（数据源接入 →
实时行情刷新 → 影子/陈旧标记 → FIFO 持仓成本计算 → 权威归属判定 →
16 项完整性检查），并打印一份汇总。

实时行情刷新这一步会为每一笔演示持仓从 yfinance/akshare 抓取真实报价——
这是最慢的一步（几分钟量级），需要网络连接。单个数据提供方偶发的抖动
（比如一次 akshare 超时）只会被记录并跳过，不会导致失败；同步仍会正常
完成。

你应该会看到类似这样的输出：

```
✅ Sync complete: 208 holdings, 346 transactions synced
Integrity gate: 14/16 passed (or 16/16 — see below)
```

（`unmatched_security_transfer` 这一项，在这份演示数据集上可能会显示为
一条提示性发现——这是这份合成转账历史本身的一个已知的、无害的特征，不是
你环境的 bug。如果只想在不跑完整同步的情况下拿到一份机器可读的重跑结果，
`--check-integrity --json` 同样可用。）

## 5. 运行应用

```bash
./dev.sh start
```

启动后端（8008 端口）和前端（5003 端口），并打开浏览器。你应该会看到一个
Dashboard，展示净资产、按数据源划分的持仓，以及一整套填满数据的
Compass / Performance / WealthOS——全部都是真实计算出来的数字，只是主体
换成了一个虚构角色，而不是你自己。

如果你不想用 `dev.sh`，也可以手动启动：

```bash
# Terminal 1
.venv/bin/python -m uvicorn src.api.main:app --reload --port 8008
# Terminal 2
cd ux-command-center && npm run dev
```

## 接下来做什么

- **接入你自己的数据**：用你的真实导出文件替换 `data/import/` 里的演示
  文件（保持相同的文件名/格式——每个数据源具体期望什么，见
  `config/readers/*.yaml`），然后重新同步。其他部分都不需要改。
- **添加一个慧眼目前还不支持的数据源**：
  [`docs/adding-a-source.md`](adding-a-source.md)（英文）——不需要改代码，
  只需要在 `src/sources/` 之外新增两个文件。
- **部署到一个持续在线的地方**：
  [`docs/deployment-instructions.md`](deployment-instructions.md)（英文）
  （Google Cloud Run + GitHub Actions）。
- **让它能长期稳定运行下去**：[`docs/operations.zh-CN.md`](operations.zh-CN.md)
  —— DuckDB 压缩、备份清理，以及为什么这两者都需要。
- **理解这个代码库的整体形态，以及以现在的认知回头看会怎么改**：
  [`docs/design-retrospective.md`](design-retrospective.md)（英文）。
