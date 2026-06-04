# RAG 知识库系统 (Query)

## 项目简介

这是一个基于 **RAG (Retrieval-Augmented Generation)** 技术的智能知识库问答系统。系统允许用户上传文档，通过 AI 向量化处理后，实现基于文档内容的智能问答。

---

## 技术栈

### 后端技术
- **Spring Boot 4.0.6** - Web 框架
- **MyBatis 4.0.1** - ORM 框架
- **MySQL 9.4** - 关系型数据库
- **Redis** - 缓存/验证码存储
- **MinIO** - 对象存储 (文档文件)
- **JWT (jjwt 0.13.0)** - 身份认证
- **Hutool 5.8.27** - 工具库
- **Lombok** - 代码简化
- **Druid** - 数据库连接池

### AI 服务 (Python)
- **FastAPI** - Web 框架
- **LangChain 0.3+** - RAG 处理流程
- **LangChain-Chroma 0.2+** - 向量数据库集成
- **LangChain-OpenAI 0.3+** - LLM 接口封装
- **ChromaDB 0.6+** - 向量数据库
- **Ollama Embeddings** - 本地 Embedding 模型
- **DeepSeek** - LLM 模型

---

## 项目结构

```
Query/
├── pom.xml                          # 根 POM (Java 25)
├── docker-compose.yml               # Docker 编排文件
├── sql/                             # 数据库初始化脚本
│   └── init_session.sql
│
├── Rag-knowledge-system/            # Java 后端模块
│   ├── pom.xml                      # 父 POM
│   │
│   ├── rag-common/                  # 公共模块
│   │   └── src/main/java/.../
│   │       ├── common/
│   │       │   ├── Result.java      # 统一返回结果
│   │       │   ├── Constants.java   # 常量定义
│   │       │   ├── JwtUtils.java    # JWT 工具类
│   │       │   ├── CurrentUser.java # 当前用户 (ThreadLocal)
│   │       │   └── config/
│   │       │       └── MinioConfig.java  # MinIO 配置
│   │       └── exception/
│   │           ├── BizException.java     # 业务异常
│   │           └── GlobalExceptionHandler.java  # 全局异常处理
│   │
│   ├── rag-domain/                  # 数据层
│   │   └── src/main/java/.../
│   │       ├── entity/              # 实体类
│   │       │   ├── User.java
│   │       │   ├── Document.java
│   │       │   ├── QaSession.java
│   │       │   ├── QaHistory.java
│   │       │   └── BaseEntity.java  # 基础实体
│   │       ├── dto/                 # 数据传输对象
│   │       │   ├── LoginRequestDTO.java
│   │       │   ├── SendCodeRequestDTO.java
│   │       │   ├── UpdateUserDTO.java
│   │       │   └── AskRequest.java
│   │       ├── mapper/              # MyBatis Mapper
│   │       │   ├── UserMapper.java
│   │       │   ├── DocumentMapper.java
│   │       │   ├── QaSessionMapper.java
│   │       │   └── QaHistoryMapper.java
│   │       └── src/main/resources/mapper/  # Mapper XML
│   │
│   ├── rag-service/                 # 业务逻辑层
│   │   └── src/main/java/.../
│   │       ├── service/
│   │       │   ├── UserService.java
│   │       │   ├── DocumentService.java
│   │       │   ├── QaService.java
│   │       │   └── FileService.java
│   │       ├── service/impl/
│   │       │   ├── UserServiceImpl.java
│   │       │   ├── DocumentServiceImpl.java
│   │       │   ├── QaServiceImpl.java
│   │       │   └── FileServiceImpl.java
│   │       └── aspect/
│   │           └── AutoFillAspect.java  # 自动填充创建时间/更新时间
│   │
│   └── rag-api/                     # 接口层 (启动入口)
│       └── src/main/java/.../
│           ├── RagKnowledgeSystemApplication.java  # 启动类
│           ├── controller/
│           │   ├── UserController.java
│           │   ├── DocumentController.java
│           │   └── QaController.java
│           ├── config/
│           │   └── WebMvcConfig.java  # Web 配置
│           └── interceptor/
│               └── LoginInterceptor.java  # 登录拦截器
│
├── docker/                          # Docker 配置
│   └── redis/
│       └── redis.conf
│
└── [Python AI 服务]                 # 独立部署
    ├── pyproject.toml               # 项目依赖配置
    ├── .env                         # 环境变量
    ├── .env.example                 # 环境变量示例
    ├── chroma_db/                   # Chroma 向量数据库存储
    └── app/
        ├── __init__.py
        ├── main.py                  # FastAPI 入口
        ├── config.py                # 配置管理
        ├── api/
        │   ├── __init__.py
        │   ├── ingestion.py         # 文档向量化 API
        │   └── qa.py                # 问答 API
        ├── core/
        │   ├── __init__.py
        │   ├── document_processor.py # 文档解析/切片
        │   ├── vector_store.py      # 向量数据库封装
        │   └── rag_engine.py        # RAG 核心引擎
        └── models/
            ├── __init__.py
            └── schemas.py           # Pydantic 数据模型
```

---

## 核心模块说明

### 1. rag-common (公共模块)
提供项目通用组件：
- `Result` - 统一 API 返回格式
- `JwtUtils` - JWT 令牌生成与解析
- `CurrentUser` - ThreadLocal 存储当前登录用户 ID
- `GlobalExceptionHandler` - 全局异常捕获

### 2. rag-domain (数据层)
定义数据结构：
- **Entity** - 数据库表映射
- **DTO** - 前后端数据传输
- **Mapper** - 数据库操作接口 + XML SQL

### 3. rag-service (业务层)
核心业务逻辑：
- **UserServiceImpl** - 用户登录/注册/信息管理
- **DocumentServiceImpl** - 文档上传/删除/向量化触发
- **QaServiceImpl** - 问答核心逻辑，调用 Python AI 服务

### 4. rag-api (接口层)
RESTful API 入口：
- Controller 定义接口路由
- LoginInterceptor 实现 JWT 认证
- WebMvcConfig 注册拦截器

### 5. Python AI 服务 (app/)
独立的 AI 微服务，提供 RAG 核心能力：

**api/ - API 接口层**
- `ingestion.py` - 文档向量化接口
  - `POST /ingest/document` - 解析文档、切片、向量化存入 Chroma
  - `DELETE /ingest/document/{id}` - 删除文档向量
- `qa.py` - 问答接口
  - `POST /qa/ask` - 执行 RAG 问答流程

**core/ - 核心业务层**
- `document_processor.py` - 文档处理器
  - 支持格式: PDF、DOCX、TXT、MD
  - 使用 `RecursiveCharacterTextSplitter` 切片 (500字符/100重叠)
  - 中文分隔符优先: `\n\n`, `\n`, `。`, `；`, `，`
- `vector_store.py` - 向量数据库封装
  - 基于 Chroma + Ollama Embeddings
  - 提供: 添加文本、相似性搜索、删除文档向量
- `rag_engine.py` - RAG 核心引擎
  - 查询改写: 模糊问题结合历史改写为自包含查询
  - 向量检索: 余弦距离阈值 0.85，过滤低分结果
  - 轮询选取: 确保各文档均匀参与
  - LLM 生成: DeepSeek 模型，温度 0.1

**models/ - 数据模型**
- `schemas.py` - Pydantic 请求/响应模型

---

## 数据库设计

### user 表
```sql
CREATE TABLE user (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    phone VARCHAR(20) NOT NULL,           -- 手机号 (登录凭证)
    username VARCHAR(50),                 -- 用户名
    password VARCHAR(100),                -- 密码 (可选)
    email VARCHAR(100),                   -- 邮箱
    role VARCHAR(20) DEFAULT 'user',      -- 角色 (user/admin)
    create_time DATETIME,
    update_time DATETIME
);
```

### document 表
```sql
CREATE TABLE document (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,              -- 所属用户
    file_name VARCHAR(255),               -- 原始文件名
    file_path VARCHAR(500),               -- MinIO 存储路径
    file_size BIGINT,                     -- 文件大小
    file_type VARCHAR(100),               -- MIME 类型
    status INT DEFAULT 0,                 -- 0:已上传 1:处理中 2:已完成 3:失败
    permission INT DEFAULT 1,             -- 0:公开 1:私有
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);
```

### qa_session 表
```sql
CREATE TABLE qa_session (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,              -- 所属用户
    title VARCHAR(200),                   -- 会话标题 (首条问题)
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);
```

### qa_history 表
```sql
CREATE TABLE qa_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,              -- 所属用户
    session_id BIGINT,                    -- 所属会话
    question TEXT,                        -- 用户问题
    answer TEXT,                          -- AI 回答
    sources TEXT,                         -- 引用来源 (JSON)
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);
```

---

## 核心业务流程

### 1. 用户登录流程

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  前端发送     │      │  后端生成     │      │  Redis 存储   │      │  返回结果     │
│  手机号      │ ───> │  6位验证码    │ ───> │  2分钟有效期  │ ───> │              │
└──────────────┘      └──────────────┘      └──────────────┘      └──────────────┘

┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  前端发送     │      │  校验验证码   │      │  查询/创建    │      │  返回 JWT    │
│  手机号+验证码│ ───> │  (Redis)     │ ───> │  用户记录     │ ───> │  Token       │
└──────────────┘      └──────────────┘      └──────────────┘      └──────────────┘
```

**关键代码:**
- `UserServiceImpl.sendCode()` - 生成验证码并存入 Redis
- `UserServiceImpl.login()` - 校验验证码，生成 JWT Token

---

### 2. 文档上传与向量化流程

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  前端上传     │      │  上传至       │      │  创建文档     │      │  触发向量化   │
│  文件        │ ───> │  MinIO       │ ───> │  记录 (DB)   │ ───> │  (Python)    │
└──────────────┘      └──────────────┘      └──────────────┘      └──────────────┘

┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  Python 服务 │      │  文档解析     │      │  存入 Chroma  │
│  接收请求    │ ───> │  文本切片     │ ───> │  向量数据库   │
└──────────────┘      └──────────────┘      └──────────────┘
```

**关键代码:**
- `DocumentServiceImpl.uploadDocument()` - 上传文档并触发向量化
- `FileServiceImpl.uploadFile()` - MinIO 文件上传

---

### 3. 智能问答流程 (RAG)

```
┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  用户提问    │      │  获取用户     │      │  组装请求     │      │  调用 Python  │
│              │ ───> │  可访问文档   │ ───> │  (问题+文档ID │ ───> │  AI 服务     │
└──────────────┘      └──────────────┘      │   +历史对话)  │      └──────────────┘
                                            └──────────────┘             │
                                                                       ▼
┌──────────────┐      ┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│  返回结果     │      │  保存问答     │      │  Python 执行  │      │  Chroma 向量  │
│  给前端      │ <──  │  历史记录     │ <──  │  RAG 流程    │ <──  │  检索        │
└──────────────┘      └──────────────┘      └──────────────┘      └──────────────┘
```

**RAG 核心步骤 (Python 服务):**
1. 接收用户问题 + 可访问的文档 ID 列表
2. 从 Chroma 向量数据库检索相关文档片段
3. 结合历史对话上下文，组装 Prompt
4. 调用 LLM (如 OpenAI/通义千问) 生成回答
5. 返回答案 + 引用来源

**关键代码:**
- `QaServiceImpl.ask()` - 问答核心逻辑
- 传递最近 5 轮历史对话用于上下文理解

---

## API 接口说明

### 用户模块 (`/user`)

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/user/send-code` | 发送验证码 | 否 |
| POST | `/user/login` | 用户登录 | 否 |
| GET | `/user/info?phone=xxx` | 获取用户信息 | 是 |
| PUT | `/user/update` | 更新用户信息 | 是 |

### 文档模块 (`/document`)

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/document/upload` | 上传文档 | 是 |
| GET | `/document/list` | 获取文档列表 | 是 |
| GET | `/document/url?id=xxx` | 获取文档预览URL | 是 |
| DELETE | `/document/delete?id=xxx` | 删除文档 | 是 |

### 问答模块 (`/qa`)

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| POST | `/qa/ask` | 提问 | 是 |
| GET | `/qa/sessions` | 获取会话列表 | 是 |
| POST | `/qa/session` | 创建新会话 | 是 |
| GET | `/qa/history?sessionId=xxx` | 获取问答历史 | 是 |
| DELETE | `/qa/session/{id}` | 删除会话 | 是 |
| DELETE | `/qa/history/{id}` | 删除问答记录 | 是 |

---

## 运行环境配置

### 1. 基础服务 (Docker)

启动 Redis 和 MinIO:
```bash
docker-compose up -d
```

**服务地址:**
- MinIO 控制台: http://localhost:9001 (账号: minioadmin/minioadmin)
- Redis: localhost:6379 (密码: sty01725)

### 2. 数据库

```sql
-- 创建数据库
CREATE DATABASE rag_knowledge DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

-- 导入表结构 (参考 sql/ 目录)
```

### 3. 后端服务

```bash
# 进入项目目录
cd Rag-knowledge-system

# 编译打包
mvn clean package -DskipTests

# 运行
java -jar rag-api/target/rag-api-0.0.1-SNAPSHOT.jar
```

**服务地址:** http://localhost:8085

### 4. Python AI 服务

```bash
# 进入 Python 项目目录
cd D:\DOWNLOAD\pycharm\query

# 激活虚拟环境
.venv\Scripts\activate

# 启动服务
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**服务地址:** http://localhost:8000
**API 文档:** http://localhost:8000/docs (Swagger UI)

---

## 配置说明

### Java 后端配置

核心配置文件: `rag-api/src/main/resources/application.yml`

```yaml
spring:
  datasource:
    url: jdbc:mysql://localhost:3306/rag_knowledge
    username: root
    password: 1234
    type: com.alibaba.druid.pool.DruidDataSource
  
  data:
    redis:
      host: localhost
      port: 6379
      password: sty01725

minio:
  endpoint: http://localhost:9000
  access-key: minioadmin
  secret-key: minioadmin
  bucket-name: rag-knowledge

jwt:
  secret: your-jwt-secret-key-at-least-256-bits-long
  expiration: 604800000  # 7天

ai-service:
  python-base-url: http://localhost:8000
```

### Python AI 服务配置

配置文件: `.env`

```env
# LLM API 配置
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL_NAME=deepseek-chat

# Embedding 模型 (Ollama)
EMBEDDING_MODEL_NAME=nomic-embed-text
EMBEDDING_DEVICE=cpu

# Ollama 服务地址
OLLAMA_BASE_URL=http://localhost:11434

# 向量数据库存储路径
VECTOR_STORE_PATH=./chroma_db

# 服务配置
HOST=0.0.0.0
PORT=8000
```

---

## 安全机制

1. **JWT 认证** - 除登录接口外，所有接口需携带 `Authorization: Bearer <token>`
2. **LoginInterceptor** - 拦截器校验 Token 有效性
3. **CurrentUser** - ThreadLocal 存储当前用户，防止越权
4. **文档权限** - 私有文档仅所有者可访问，公开文档所有人可见

---

## 扩展点

1. **LLM 模型** - 当前使用 DeepSeek，可切换为 OpenAI、通义千问等
2. **Embedding 模型** - 当前使用 Ollama 本地模型，可切换为 OpenAI Embedding
3. **文档类型** - 当前支持 PDF/DOCX/TXT/MD，可扩展 PPT、Excel 等
4. **权限系统** - 可扩展 RBAC 角色权限控制
5. **多租户** - 可基于 document_id 实现数据隔离

---

## 附录: LangChain 依赖版本

```
langchain>=0.3
langchain-community>=0.3
langchain-huggingface>=0.1
langchain-chroma>=0.2
langchain-openai>=0.3
chromadb>=0.6
```

---

*最后更新: 2026-06-02*
