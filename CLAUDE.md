# Query - RAG 知识库问答系统 (Python AI 服务)

## 系统架构

本项目是一个 **RAG (Retrieval-Augmented Generation) 智能知识库问答系统**，由三部分组成：

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | `D:\IntelliJ IDEA 2025.1.3\project\Query` | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | 本项目 (`D:\DOWNLOAD\pycharm\query`) | FastAPI + LangChain + ChromaDB | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

本项目是系统的 AI 核心，负责文档向量化和 RAG 问答，由 Java 后端通过 HTTP 调用。

---

## 项目结构

```
query/
├── pyproject.toml               # 项目依赖配置
├── .env                         # 环境变量 (API Key, 模型配置)
├── .env.example                 # 环境变量示例
├── chroma_db/                   # Chroma 向量数据库持久化存储
├── ai_service.log               # 服务日志
└── app/
    ├── main.py                  # FastAPI 入口 (uvicorn 启动)
    ├── config.py                # 配置管理 (读取 .env)
    ├── api/
    │   ├── ingestion.py         # 文档向量化 API
    │   └── qa.py                # 问答 API
    ├── core/
    │   ├── document_processor.py # 文档解析/切片
    │   ├── vector_store.py      # 向量数据库封装
    │   └── rag_engine.py        # RAG 核心引擎
    └── models/
        └── schemas.py           # Pydantic 数据模型
```

## 模块职责

### api/ - 接口层
- `ingestion.py`:
  - `POST /ingest/document` - 接收文档内容，解析、切片、向量化存入 Chroma
  - `DELETE /ingest/document/{id}` - 删除指定文档的向量
- `qa.py`:
  - `POST /qa/ask` - 执行 RAG 问答流程 (查询改写 → 向量检索 → LLM 生成)

### core/ - 核心业务层
- `document_processor.py` - 文档处理器
  - 支持格式: PDF、DOCX、TXT、MD、XLSX
  - Excel 每行视为独立 chunk，不做文本切片
  - `RecursiveCharacterTextSplitter` 切片 (1000字符/200重叠，非 Excel 文档)
  - 中文分隔符优先: `\n\n`, `\n`, `。`, `；`, `，`
- `vector_store.py` - 向量数据库封装
  - Chroma + Ollama Embeddings (nomic-embed-text)
  - 提供: add_texts, similarity_search, delete_document
- `rag_engine.py` - RAG 核心引擎
  - 查询改写: 模糊问题结合历史改写为自包含查询
  - 向量检索: 余弦距离阈值 0.85，过滤低分结果
  - 轮询选取: 确保各文档均匀参与
  - LLM 生成: DeepSeek (deepseek-chat)，温度 0.1

### models/ - 数据模型
- `schemas.py` - Pydantic 请求/响应模型

### config.py - 配置管理
从 `.env` 加载: LLM_API_KEY, LLM_BASE_URL, LLM_MODEL_NAME, EMBEDDING_MODEL_NAME, OLLAMA_BASE_URL, VECTOR_STORE_PATH 等。

---

## 外部依赖服务

- **Ollama**: localhost:11434 (本地 Embedding 模型: nomic-embed-text)
- **DeepSeek API**: api.deepseek.com (LLM 推理)
- **ChromaDB**: 本地持久化存储 (`./chroma_db/`)

Java 后端通过 `http://localhost:8000` 调用本服务的 API。

---

## 开发指南

### 启动服务
```bash
# 激活虚拟环境
.venv\Scripts\activate

# 启动 (开发模式，热重载)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**API 文档:** http://localhost:8000/docs (Swagger UI)

### 环境变量配置 (.env)
```env
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat
EMBEDDING_MODEL_NAME=nomic-embed-text
EMBEDDING_DEVICE=cpu
OLLAMA_BASE_URL=http://localhost:11434
VECTOR_STORE_PATH=./chroma_db
HOST=0.0.0.0
PORT=8000
```

### 依赖管理
```bash
# 项目使用 pyproject.toml 管理依赖
pip install -e .
```

### 代码规范
- Python 3.12+, FastAPI 框架
- 类型注解 + Pydantic 模型校验
- LangChain 0.3+ 生态 (langchain, langchain-chroma, langchain-openai)
- 异步 API (FastAPI async)
- 日志输出到 `ai_service.log`

### 修改注意事项
- 文档切片参数修改在 `core/document_processor.py` (chunk_size, chunk_overlap)
- RAG 检索阈值修改在 `core/rag_engine.py` (余弦距离阈值 0.85)
- Embedding 模型切换需修改 `.env` 中的 `EMBEDDING_MODEL_NAME` 和 `OLLAMA_BASE_URL`
- LLM 模型切换需修改 `.env` 中的 `LLM_MODEL_NAME` 和 `LLM_BASE_URL`
- 修改 API 路径需同步更新 Java 后端 `QaServiceImpl` 和 `DocumentServiceImpl` 中的 HTTP 调用
