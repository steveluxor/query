# Query - Agent Runtime 架构 (v14: Dense + BM25 混合检索)

> 基线为 v13 的 Redis checkpoint、lease 恢复与运行态隔离。本版只扩展 Retrieval Capability，不改变 Planner + DAG Runtime 的职责边界。

## 从 v13 到 v14 的变更

| 维度 | v13 | v14 |
|------|-----|-----|
| 稀疏召回 | Chroma 全量扫描的关键词包含匹配 | 独立 RediSearch 倒排索引，BM25 排序 |
| 召回融合 | 向量结果与关键词候选的启发式补入 | Dense + BM25 两路排名按 RRF 融合 |
| 索引关联 | Chroma 内部 ID 与业务切片关系不固定 | `chunk_id=doc:{document_id}:chunk:{chunk_index}` 关联双索引 |
| 历史数据 | 只有 Chroma 向量 | 回填 `chunk_id`，全量重建 RediSearch 索引 |
| 入库一致性 | 仅维护 Chroma | 上传、覆盖、删除同步维护 Chroma 与 RediSearch |
| 运行依赖 | Python AI 等待 Ollama | Python AI 同时等待 Ollama、RediSearch 健康 |
| Docker 服务 | 9 个 | 10 个，新增 `redisearch` |

## 检索架构

```text
Planner / RetrievalAgent
  -> LLM 生成 query 与查询类型
  -> jieba 提取有效关键词
  -> Chroma Dense Top-60 -----------+
  -> RediSearch BM25 Top-60 --------+-> RRF(chunk_id) -> 动态 Top-K / 多样性
                                                        -> 文件名补充
                                                        -> 摘要相关性过滤
                                                        -> read_all_rows 全量读取最终相关文档
```

### 1. 查询边界

- Java 鉴权后将当前用户可访问的 `document_id` 写入 MCP Session。
- Chroma 的 `where document_id in (...)` 与 RediSearch 的精确 numeric OR 条件使用同一范围。
- RediSearch 不能使用数值范围 `@document_id:[1 3]` 代替多文档过滤，否则会错误包含 ID 为 2 的未授权文档；实现使用 `(@document_id:[1 1]|@document_id:[3 3])`。

### 2. 两路召回与 RRF

Dense 召回擅长语义相近但字面不同的问题；BM25 擅长术语、字段名和文件中明确出现的词。两路各自只提供排名，不直接比较原始分数：向量距离与 BM25 分数不在同一量纲。

```text
rrf(chunk) = sum(1 / (60 + rank_i))
```

同一 `chunk_id` 在两路排名中去重并累加分数，结果继续沿用 RAG Engine 的动态 Top-K 和文档多样性选择。文件名匹配不是 BM25 的替代品：它是针对“用户直接点名文件”的独立兜底。

### 3. 入库与历史回填

新文档切片写入 Chroma 时，metadata 与 Chroma ID 都使用稳定 `chunk_id`。随后按该 `document_id` 从 Chroma 取回正文和 metadata，写入 RediSearch Hash：

```text
rag:bm25:chunk:{chunk_id}
  chunk_id / document_id / file_name / terms / content
```

- `terms`：正文经 `jieba` 分词后的空格分隔词项，参与 BM25 倒排索引。
- `content`：只存储、返回给检索层，不参与索引。
- 历史库运行 `python scripts/rebuild_sparse_index.py`：为旧 Chroma metadata 回填 `chunk_id`，清除本系统前缀的旧稀疏键，再重建全部索引。
- 删除文档时，先删 Chroma，再按精确 `document_id` 删除对应 RediSearch Hash。

## 服务与持久化边界

| 服务 | 责任 | 持久化 |
|------|------|------|
| `redis` | checkpoint、lease、Stream、会话缓存、限流 | `redis-data` |
| `redisearch` | BM25 稀疏索引 | `redisearch-data` + AOF |
| `python-ai` | Dense/BM25 查询、RRF、Agent Runtime | 无业务持久化 |
| `stream-consumer` | 异步解析与入库任务 | 无 |

`redisearch` 不映射宿主机端口，仅在 `rag-network` 内被 `python-ai` 访问；`python-ai` 的 Compose `depends_on` 要求它健康后才启动。这样不会把检索索引和运行态 Redis 混在同一实例，也避免冷启动时 MCP 初始化连接到未就绪服务。

## 观测与验证

RAG 日志记录以下边界信息：

```text
提取关键词
embedding 返回 / 阈值后数量
BM25 命中数量
RRF 融合后数量
文件名候选与补入
摘要过滤前后文档数
read_all_rows 加载的最终全量 chunk 数
```

本次上线验证包括：RediSearch `search` 模块可用、BM25 查询、精确 ACL 过滤、删除同步、RRF 去重、Compose 配置，以及历史 Chroma 数据回填。验证快照中稀疏索引为 372 个 chunk，`FT.INFO` 显示 `percent_indexed=1`、`indexing failures=0`。

## 已知边界与后续工作

1. 没有 Golden Set，不能宣称召回率或答案准确率提升；当前只验证功能和可观测性。
2. 文档摘要过滤是 LLM 相关性过滤，不是 Cross-Encoder reranker；不能表述为重排序模型。
3. Chroma 与 RediSearch 是双写，尚无 Outbox、重试队列或定时对账；单次稀疏写失败时查询降级为 Dense-only，但最终一致性仍需补偿机制。
4. RediSearch 用于稀疏 BM25，不解决 Chroma 嵌入式多进程写入的单写者边界。

## v14 面试表述

> 我没有把关键词检索继续放在 Chroma 全量扫描里，而是把 Dense 和 Sparse 拆到两个职责明确的索引：Chroma 做语义向量召回，RediSearch 按 jieba 分词维护 BM25 倒排索引。因为两者的分数不可直接比较，我以稳定 chunk ID 对齐排名后使用 RRF 融合。入库、覆盖、删除同步更新双索引，历史数据通过回填脚本重建；检索始终带 document_id 权限过滤，RediSearch 故障时回退 Dense-only。当前没有 Golden Set，所以我只主张完成了混合检索工程化，不宣称效果提升百分比。
