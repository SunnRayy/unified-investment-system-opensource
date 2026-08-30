[English](operations.md) | 简体中文

# 运维

慧眼基本不需要人日常盯着——但 DuckDB 的存储模型意味着「基本不需要盯」
不等于「完全不需要维护」，跳过下面这些维护动作，最终会让原本只有几
MB 实际数据的数据库文件，膨胀到几百 MB。这篇文档解释这是为什么发生的，
以及能防止它变成问题的那三条命令。

## 为什么需要维护：DuckDB 不会自动收缩

DuckDB 是一个列式存储、MVCC（多版本并发控制）数据库。由此带来两个结构性
事实，两者都不是 bug：

1. **列式存储本身有按列、按 row group 计的开销。** 一个包含很多表和列的
   schema，在还没写入任何数据之前，就已经背负了实打实的结构性重量。
2. **每一次 `UPDATE`/`DELETE` 都会留下一个死亡版本。** MVCC 会把旧版本
   的行保留下来（用于快照隔离），直到有什么东西显式地回收它——普通的
   `CHECKPOINT` 或 `VACUUM ANALYZE` **都不会**做这件事。慧眼的同步流水线
   在每次运行时都会删除并重新插入持仓行（因为持仓在变化），所以死亡版本
   会以稳定的速率累积——在一个被频繁使用的实例上，每次同步增加几 MB
   并不罕见。

放任不管的话，`.duckdb` 文件会大致随同步次数线性增长，而不是随实际投资
组合规模增长。这是存储引擎的一个已知的、有其存在意义的特性，不是未来某次
迁移就能消除的东西——这正是为什么压缩（compaction）是一项常规操作，而不
是一次性修复。

## 三条常规操作

三条命令都在 `scripts/maint_db.py` 里，并且默认都是 dry-run（试运行）——
它们会打印将要做什么，但除非你加上 `--execute`，否则不会真的改动任何
东西。

### 压缩（回收 MVCC 死亡版本）

```bash
./dev.sh stop && .venv/bin/python scripts/maint_db.py --compact-local && ./dev.sh start
```

把数据库导出为 Parquet，再重新导入一个全新的文件——这是真正回收上面
说的那些死亡版本开销的唯一方式。需要先停掉应用（它需要对数据库文件的
独占访问权）。对于部署在 Cloud Run 上的实例：`--compact-cloud` 对存放在
GCS 上的数据库做同样的事情，并重启该服务。

多久做一次：没有固定的排期——当 `pragma_database_size()` 看起来和你实际
的持仓数量不成比例时，或者按你自己同步频率觉得合理的节奏来压缩即可。
不做也不会出问题，只是文件会一直变大。

### 清理备份

```bash
.venv/bin/python scripts/maint_db.py --prune-backups            # dry-run
.venv/bin/python scripts/maint_db.py --prune-backups --execute  # actually delete
```

每次 `--sync-v3` 和 `--compact-*` 运行都会先做一次完整备份
（`data/backups/`，或云端对应位置）——这是安全网，不是可选项。备份会以
和主数据库同样的方式累积，只不过是以整份额外拷贝的形式，而不是死亡行
版本的形式。清理会保留一个有上限的最近集合（默认保留最新 8 份），并且
只删除约 1 GiB 以下的备份，这样即便清理逻辑出了 bug，也不可能悄悄删掉
一份又大又重要的备份。

**备份在其他情况下只能由人手动删除**——除了这条显式的、默认 dry-run 的
命令之外，没有任何自动化流程会删除它们。

### `--all`

`.venv/bin/python scripts/maint_db.py --all` 依次运行清理备份 +
compact-local + compact-cloud。pre-push git hook
（`scripts/git-hooks/pre-push`）会在每次推送到 release 分支时自动运行
备份清理——每台机器只需要安装一次：

```bash
cp scripts/git-hooks/pre-push .git/hooks/ && chmod +x .git/hooks/pre-push
```

## 相关命令（不算维护，但相关）

```bash
python main.py --backup            # one-off manual backup, independent of a sync
python main.py --list-backups      # what backups exist and when
python main.py --check-integrity   # the 16 invariant checks, standalone (no sync)
```

## 数据库安全

这个项目视为不可妥协的几条规则，一部分靠开发环境里的 pre-tool-use hook
强制执行，一部分靠约定：

- 在对一个你在意的数据库执行 `--init`/`--reset`、`DROP TABLE`、
  `TRUNCATE` 或 `DELETE FROM` 之前，一定要先做一份新的备份。
- 同步之前先确认数据库里确实有真实数据（行数超过某个合理的下限）——
  往一个意外变空的数据库里同步，正是一个真实实例的数据曾经丢失过的
  原因。如果一个数据库看起来空得不正常，先停下来排查，而不是直接同步
  进去。
- 备份是留给人来删除的，不是留给脚本删除的（`--prune-backups` 是唯一被
  认可的例外，而且如上所述，它是有上限、默认 dry-run 的）。
