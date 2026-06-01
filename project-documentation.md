# AI RAG 企业智能文档问答系统

## 项目简介

基于 RAG（Retrieval-Augmented Generation）架构的企业文档问答系统，由 **Java Spring Boot 后端 + Python FastAPI AI 微服务 + Nginx 前端** 三部分组成。

用户在 Web 端上传文档后，系统自动解析、切片、向量化存入知识库；用户提问时，系统从知识库中检索相关片段，交给 LLM 生成答案，并保存问答历史。

---

## 目录结构

```
query/
├── app/                              # Python AI 微服务 (FastAPI)
│   ├── main.py                       # 应用入口 + lifespan 初始化
│   ├── config.py                     # pydantic-settings 配置
│   ├── api/
│   │   ├── ingestion.py              # 文档入库/删除接口
│   │   └── qa.py                     # 问答接口
│   ├── core/
│   │   ├── document_processor.py     # 文档解析 + 切片
│   │   ├── vector_store.py           # ChromaDB 向量库封装
│   │   └── rag_engine.py             # RAG 引擎（检索 → Prompt → LLM）
│   └── models/
│       └── schemas.py                # Pydantic 数据模型
│
├── rag-knowledge-system/             # Java 后端 (Spring Boot)
│   ├── pom.xml                       # Maven 依赖
│   ├── rag-api/                      # API 层
│   │   └── src/main/java/.../
│   │       ├── controller/
│   │       │   ├── UserController.java      # 登录/注册/验证码
│   │       │   ├── DocumentController.java  # 文档上传/列表/删除
│   │       │   └── QaController.java        # 问答/历史/删除历史
│   │       ├── interceptor/
│   │       │   └── LoginInterceptor.java    # JWT 登录拦截
│   │       └── config/
│   │           └── WebMvcConfig.java        # 拦截器注册
│   ├── rag-service/                  # 业务逻辑层
│   │   └── src/main/java/.../service/
│   │       ├── impl/UserServiceImpl.java
│   │       ├── impl/DocumentServiceImpl.java  # 上传 → MinIO → 调 Python
│   │       ├── impl/FileServiceImpl.java      # MinIO 文件操作
│   │       └── impl/QaServiceImpl.java        # 问答 → 调 Python → 存历史
│   ├── rag-domain/                   # 实体 + 数据访问
│   │   └── src/main/java/.../
│   │       ├── entity/
│   │       │   ├── User.java                 # 用户
│   │       │   ├── Document.java             # 文档（含 permission 权限）
│   │       │   └── QaHistory.java            # 问答历史
│   │       ├── dto/
│   │       │   ├── LoginRequestDTO.java
│   │       │   ├── AskRequest.java
│   │       │   └── ...
│   │       ├── mapper/
│   │       │   ├── UserMapper.java
│   │       │   ├── DocumentMapper.java
│   │       │   └── QaHistoryMapper.java
│   │       └── resources/mapper/             # MyBatis XML
│   └── rag-common/                   # 公共工具
│       └── src/main/java/.../common/
│           ├── Result.java                   # 统一响应格式
│           ├── JwtUtils.java                 # JWT 生成/解析
│           ├── CurrentUser.java              # ThreadLocal 持有用户 ID
│           └── Constants.java                # 常量
│
├── nginx-query/                      # 前端 (Nginx 静态页面)
│   ├── html/
│   │   ├── index.html                # 主页面
│   │   ├── css/style.css
│   │   └── js/
│   │       ├── api.js                # API 请求封装
│   │       └── app.js                # 前端逻辑
│   └── conf/
│       └── nginx.conf                # Nginx 配置
│
├── pyproject.toml                    # Python 依赖
└── .env.example                      # 环境变量模板
```

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────┐
│                        前端 (Nginx)                           │
│                  html + css + js (静态页面)                    │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP (8085)
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                    Java 后端 (Spring Boot)                     │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ UserController│  │Document     │  │ QaController     │   │
│  │ /user/xxx     │  │Controller   │  │ /qa/ask          │   │
│  │               │  │ /doc/upload  │  │ /qa/history      │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                │                   │              │
│         ▼                ▼                   ▼              │
│  ┌────────────────────────────────────────────────────┐     │
│  │                 Service 层                          │     │
│  │  UserService  │  DocumentService  │  QaService     │     │
│  └────────────────────────────────────────────────────┘     │
│         │                │                   │              │
│         ▼                ▼                   ▼              │
│  ┌────────────┐  ┌────────────┐  ┌────────────────────┐    │
│  │  MySQL     │  │  MinIO     │  │  Redis             │    │
│  │ 用户/文档/ │  │  文件存储  │  │  验证码缓存        │    │
│  │ 问答历史   │  │           │  │                    │    │
│  └────────────┘  └────────────┘  └────────────────────┘    │
│                                                              │
│  中间件: JWT (认证) │ ThreadLocal (请求隔离) │ MyBatis (ORM)  │
└──────────────────────┬───────────────────────────────────────┘
                       │ HTTP (8000) — Java 主动调用 Python
                       ▼
┌──────────────────────────────────────────────────────────────┐
│                 Python AI 微服务 (FastAPI)                     │
│                                                              │
│  POST /ingest/document      ← 文档入库请求                    │
│  DELETE /ingest/document/id ← 文档删除请求                    │
│  POST /qa/ask               ← 问答请求                       │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  DocumentProcessor     RAGEngine                     │   │
│  │  ┌──────────────┐     ┌────────────────────────┐    │   │
│  │  │ PyPDFLoader  │     │ VectorStore.similarity │    │   │
│  │  │ Docx2txt     │     │    _search()           │    │   │
│  │  │ TextLoader   │     │   ↓                    │    │   │
│  │  │ Recursive    │     │ 构建 Prompt            │    │   │
│  │  │ TextSplitter │     │   ↓                    │    │   │
│  │  └──────┬───────┘     │ ChatOpenAI(DeepSeek)   │    │   │
│  │         │             └────────────────────────┘    │   │
│  │         ▼                                           │   │
│  │  ┌──────────────────────────────────────────────┐  │   │
│  │  │        ChromaDB (本地向量库)                   │  │   │
│  │  │        Ollama Embeddings (本地嵌入)            │  │   │
│  │  └──────────────────────────────────────────────┘  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  外部依赖: DeepSeek Chat API (LLM 生成回答)                   │
└──────────────────────────────────────────────────────────────┘
```

---

## 技术栈

### Java 后端

| 组件 | 技术 |
|------|------|
| 框架 | Spring Boot 3 |
| ORM | MyBatis |
| 数据库 | MySQL（用户、文档元数据、问答历史） |
| 缓存 | Redis（验证码） |
| 文件存储 | MinIO（文档文件） |
| 认证 | JWT（jjwt 库） |
| 请求隔离 | ThreadLocal（CurrentUser） |

### Python AI 服务

| 组件 | 技术 |
|------|------|
| Web 框架 | FastAPI |
| AI 编排 | LangChain |
| 向量数据库 | ChromaDB（本地持久化） |
| 嵌入模型 | nomic-embed-text（Ollama 本地运行） |
| 文档解析 | PyPDFLoader、Docx2txtLoader、TextLoader |
| 文本切片 | RecursiveCharacterTextSplitter（500字符，重叠100） |
| LLM | DeepSeek Chat API（通过 ChatOpenAI 兼容层调用） |

### 前端

| 组件 | 技术 |
|------|------|
| 页面 | 原生 HTML + CSS + JavaScript |
| Web 服务器 | Nginx |
| API 通信 | Fetch API |

---

## 核心流程

### 流程一：用户登录

```
POST /user/sendCode  ──→ Redis 存验证码（key: CODE:手机号）
POST /user/login     ──→ 校验验证码 → 生成 JWT Token → 返回给前端

前端后续请求将 token 放在 header 中:
  Authorization: Bearer xxx
```

### 流程二：文档上传 & 向量化入库

```
用户上传文件
    │
    ▼
POST /document/upload (Java)
    │
    ├── ① 文件存入 MinIO
    ├── ② Document 记录写入 MySQL（状态: UPLOADED）
    ├── ③ 从 MinIO 下载到临时文件
    ├── ④ HTTP 调用 Python /ingest/document
    │         │
    │         ▼
    │   DocumentProcessor.process()
    │     ├── 按后缀识别类型 (.pdf / .docx / .txt / .md)
    │     ├── 解析为纯文本
    │     └── RecursiveCharacterTextSplitter 切片
    │           （chunk_size=500, chunk_overlap=100）
    │         │
    │         ▼
    │   VectorStore.add_texts()
    │     ├── OllamaEmbeddings 本地向量化
    │     └── 存入 ChromaDB（metadata 含 document_id）
    │
    ├── ⑤ 更新 MySQL 状态: COMPLETED / FAILED
    ├── ⑥ 删除临时文件
    └── 返回文档信息给前端
```

### 流程三：用户提问 & 回答

```
用户输入问题
    │
    ▼
POST /qa/ask (Java)
    │
    ├── ① CurrentUser.get() 获取当前用户 ID（来自 JWT）
    │
    ├── ② 查用户有权限的文档（公开 + 自己的私有）
    │     documentMapper.selectByUserId(userId)
    │     → 提取 document_id 列表
    │
    ├── ③ 调用 Python /qa/ask { question, document_ids }
    │         │
    │         ▼
    │   RAGEngine.answer()
    │     ├── VectorStore.similarity_search()
    │     │   filter={"document_id": {"$in": [...]}}
    │     │   → ChromaDB 语义检索（只搜权限范围内的文档）
    │     │   → 返回 (Document, score) 列表
    │     │
    │     ├── 拼接 context（检索结果去重）
    │     ├── 构建 Prompt（模板 + context + question）
    │     ├── ChatOpenAI(DeepSeek) 调用 LLM
    │     └── 返回 { answer, sources[] }
    │
    ├── ④ 保存问答历史到 MySQL QaHistory
    │     userId, question, answer, sources(JSON)
    │
    └── 返回 { answer, sources } 给前端
```

### 流程四：查看问答历史

```
GET /qa/history (Java)
    │
    ├── CurrentUser.get() 获取用户 ID
    └── qaHistoryMapper.selectByUserId(userId)
         → 只返回该用户的记录
```

### 流程五：删除文档

```
DELETE /document/{id} (Java)
    │
    ├── 校验: 仅文档所有者可删除
    ├── DELETE /ingest/document/{id} (Python) → 删除 ChromaDB 中对应切片
    ├── MinIO 删除文件
    └── MySQL 删除记录
```

---

## 权限模型

```
Document 实体:
  permission = 0  → 公开文档（所有用户可检索）
  permission = 1  → 私有文档（仅上传者可检索）

问答时的文档过滤:
  QaServiceImpl.ask()
    → documentMapper.selectByUserId(userId)
    → 返回该用户可访问的文档（公开 + 自己的私有）
    → 将这些文档的 ID 传给 Python 做 filter
    → 其他文档不会被检索到
```

---

## 部署配置

### Python AI 服务

```bash
# 启动 Ollama（需要先下载 nomic-embed-text 模型）
ollama pull nomic-embed-text
ollama serve

# 启动 Python 服务
cd query
pip install -r requirements.txt  # 或用 poetry install
uvicorn app.main:host --reload --port 8000
```

### Java 后端

```bash
# 需要 MySQL、Redis、MinIO 服务运行
cd rag-knowledge-system
mvn clean package -DskipTests
java -jar rag-api/target/*.jar
```

### 前端

```bash
# Nginx 直接指向 nginx-query 目录
# 配置反向代理到 Java 后端 (8085)
```

---

## API 接口一览

### Java 后端 (port 8085)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /user/login | 用户登录 |
| POST | /user/sendCode | 发送验证码 |
| POST | /document/upload | 上传文档 |
| GET | /document/list | 文档列表 |
| GET | /document/{id}/url | 获取文档 URL |
| DELETE | /document/{id} | 删除文档 |
| POST | /qa/ask | 提问 |
| GET | /qa/history | 问答历史 |
| DELETE | /qa/history/{id} | 删除历史记录 |

### Python AI 服务 (port 8000)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /ingest/document | 文档入库（向量化） |
| DELETE | /ingest/document/{id} | 删除文档向量 |
| POST | /qa/ask | RAG 问答 |
| GET | /health | 健康检查 |

---

## 关键设计要点

1. **跨服务通信**：Java 端通过 `HttpClient` 主动调用 Python AI 服务，Python 不保存任何业务状态
2. **用户隔离**：JWT + ThreadLocal 确保每个请求只能访问自己的数据
3. **文档权限**：Java 端先过滤权限，再把白名单传给 Python 做检索过滤
4. **Embedding 本地化**：通过 Ollama 运行 nomic-embed-text，无需外部 API
5. **LLM 按需使用**：Prompt 设计为"有文档引文档，无文档用自身知识"
