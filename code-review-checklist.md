# 项目复盘清单

> 按请求流向组织，从全局到细节

---

## 第一遍：全貌认知

### 1. 项目总览

| 顺序 | 文件 | 看点 |
|------|------|------|
| 1 | `docker-compose.yml` | 9 个服务的依赖关系、网络拓扑 |
| 2 | `sql/init.sql` | 4 张表：user → document → qa_session → qa_history |
| 3 | `nginx/conf/nginx-docker.conf` | 请求入口：`/user/` → Java、`/document/` → Java、`/qa/` → Java、`/` → 前端静态页面 |

---

## 第二遍：Python AI 服务（核心业务）

### 2. 配置与入口

| 顺序 | 文件 | 看点 |
|------|------|------|
| 4 | `app/config.py` | LLM / Embedding / RabbitMQ / MinIO 配置 |
| 5 | `app/main.py` | FastAPI 启动、CORS、路由注册 |

### 3. 文档处理（上传 → 向量化链路）

| 顺序 | 文件 | 看点 |
|------|------|------|
| 6 | `app/api/ingestion.py` | `POST /ingest/document` + `DELETE /ingest/document/{id}` |
| 7 | `app/core/document_processor.py` | 支持 5 种格式（PDF/DOCX/DOC/TXT/MD/XLSX）、切片策略、Excel 逐行转 key:value |
| 8 | `app/core/vector_store.py` | ChromaDB 封装、相似度检索、压缩重建索引 |
| 9 | `app/stream_consumer.py` | **RabbitMQ 消费者**：下载 MinIO → 调 ingest API → 更新状态（含断线重连） |

### 4. RAG 问答链路（核心中的核心）

| 顺序 | 文件 | 看点 |
|------|------|------|
| 10 | `app/api/qa.py` | `POST /qa/ask`：接收问题+历史，调 RAGEngine |
| 11 | `app/models/schemas.py` | 请求/响应 Pydantic 模型 |
| 12 | **`app/core/rag_engine.py`** | **整个项目最重要的文件（~427 行）** |
|     | → `_needs_rag()` | 意图判断：闲聊直接回答 vs 需要检索 |
|     | → `_rewrite_query()` | 查询改写：缩写补全、结合历史 |
|     | → `similarity_search()` | 向量检索：TOP 60 → 阈值 0.85 过滤 |
|     | → `_determine_top_k()` | 动态选块：分数间隙分析 |
|     | → `_select_by_diversity()` | 多样性轮询：保证多文档覆盖 |
|     | → `_filename_fallback()` | 文件名关键词匹配回退 |
|     | → `answer()` | 完整流程编排 |

---

## 第三遍：Java 后端（协调层）

### 5. 构建与配置

| 顺序 | 文件 | 看点 |
|------|------|------|
| 13 | `java-backend/pom.xml` | 父 pom，4 个 module |
| 14 | `java-backend/rag-api/src/main/resources/application.yml` | 本地环境配置 |
| 15 | `java-backend/rag-api/src/main/resources/application-docker.yml` | Docker 环境（服务名指向容器） |

### 6. 入口与全局

| 顺序 | 文件 | 看点 |
|------|------|------|
| 16 | `RagKnowledgeSystemApplication.java` | Spring Boot 启动类 |
| 17 | `common/Constants.java` | 全项目常量：MQ exchange/queue、状态枚举、锁前缀 |
| 18 | `common/Result.java` | 统一响应封装 |
| 19 | `exception/GlobalExceptionHandler.java` | 全局异常处理 |
| 20 | `config/WebMvcConfig.java` | 拦截器注册 + `/document/*/status` 白名单 |

### 7. 拦截器与安全

| 顺序 | 文件 | 看点 |
|------|------|------|
| 21 | `interceptor/LoginInterceptor.java` | JWT 登录校验 |
| 22 | `interceptor/RateLimitInterceptor.java` | Redis 限流 |
| 23 | `common/JwtUtils.java` | JWT 生成与验证 |

### 8. 数据层（Entity + Mapper）

| 顺序 | 文件 | 看点 |
|------|------|------|
| 24 | `entity/User.java`、`entity/Document.java` | 核心实体 |
| 25 | `entity/QaSession.java`、`entity/QaHistory.java` | 问答会话与历史 |
| 26 | `entity/BaseEntity.java` | 公共字段（create_time、update_time） |
| 27 | `mapper/UserMapper.java` → `UserMapper.xml` | MyBatis 映射 |
| 28 | `mapper/DocumentMapper.java` → `DocumentMapper.xml` | 含动态 SQL 更新 |
| 29 | `mapper/QaSessionMapper.java` + `QaHistoryMapper.java` | 对应 XML |

### 9. 业务层（Service）

| 顺序 | 文件 | 看点 |
|------|------|------|
| 30 | **`service/impl/DocumentServiceImpl.java`** | **Java 端最核心**：上传→MinIO→RabbitMQ、覆盖上传、重新向量化 |
| 31 | `service/impl/QaServiceImpl.java` | 调用 Python AI 的 `/qa/ask`，保存历史 |
| 32 | `service/impl/UserServiceImpl.java` | 登录注册、手机验证码 |
| 33 | `service/impl/FileServiceImpl.java` | MinIO 上传/下载/删除 |
| 34 | `aspect/AutoFillAspect.java` | AOP 自动填充 create_time / update_time |
| 35 | `common/config/MinioConfig.java` | MinIO 客户端配置 |
| 36 | **`common/config/RabbitMQConfig.java`** | **MQ 架构**：exchange/queue/binding 声明 |

### 10. 控制层（Controller）

| 顺序 | 文件 | 看点 |
|------|------|------|
| 37 | `controller/DocumentController.java` | 文档 CRUD + 覆盖上传 |
| 38 | `controller/QaController.java` | 问答 + 会话/历史管理 |
| 39 | `controller/UserController.java` | 登录注册 |

### 11. DTO 与测试

| 顺序 | 文件 | 看点 |
|------|------|------|
| 40 | `dto/AskRequest.java`、`LoginRequestDTO.java` 等 | 请求体定义 |
| 41 | `RedisConnectionTest.java`、`RagKnowledgeSystemApplicationTests.java` | 测试 |

---

## 第四遍：Docker / 部署

### 12. 容器化

| 顺序 | 文件 | 看点 |
|------|------|------|
| 42 | `Dockerfile`（根目录） | Python 多阶段构建 |
| 43 | `java-backend/Dockerfile` | Java 多阶段构建（Maven 缓存优化） |
| 44 | `.dockerignore` | 构建上下文排除 |
| 45 | `docker-compose.yml`（第二遍回顾） | 完整拓扑回顾 |

---

## 请求流向总结

```
用户请求
  → Nginx (:8080)
    → Java Controller（拦截器校验）
      → Java Service（业务逻辑）
        → Python AI Service（RAG 核心）
          → ChromaDB 检索
            → LLM 生成
```

## 面试重点

- **`rag_engine.py`** 的每一步：怎么改写、怎么检索、阈值为什么 0.85、为什么动态 top_k
- **`stream_consumer.py`** 为什么用 RabbitMQ 而非 Redis Stream
- **`document_processor.py`** 切片策略（chunk_size / chunk_overlap 为什么这么设）
- **`DocumentServiceImpl.java`** 上传→MQ→异步向量化全链路
- **`RabbitMQConfig.java`** + `stream_consumer.py` 配成一对，理解 MQ 拓扑
