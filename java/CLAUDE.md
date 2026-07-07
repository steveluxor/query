# Query - RAG 知识库问答系统 (Java 后端)

## 系统架构

本项目是一个 **RAG (Retrieval-Augmented Generation) 智能知识库问答系统**，由三部分组成：

| 组件 | 路径 | 技术栈 | 端口 |
|------|------|--------|------|
| 前端 | `D:\DOWNLOAD\nginx-query` | Nginx + 原生 HTML/CSS/JS | :8080 |
| Java 后端 | 本项目 (`D:\IntelliJ IDEA 2025.1.3\project\Query`) | Spring Boot 4.0.6 + MyBatis | :8085 |
| Python AI 服务 | `D:\DOWNLOAD\pycharm\query` | FastAPI + LangChain + ChromaDB | :8000 |

**请求流向：** 前端(:8080) → Nginx 反向代理 → Java 后端(:8085) → Python AI 服务(:8000)

Nginx 将 `/user/`、`/document/`、`/qa/` 路径代理到 Java 后端，静态文件由 Nginx 直接服务。

---

## 项目结构

```
Query/
├── pom.xml                          # 根 POM (Java 25)
├── docker-compose.yml               # Docker 编排 (Redis + MinIO)
├── sql/                             # 数据库初始化脚本
├── Rag-knowledge-system/            # Java 后端主模块
│   ├── rag-common/                  # 公共模块 (Result, JWT, 异常处理)
│   ├── rag-domain/                  # 数据层 (Entity, DTO, Mapper)
│   ├── rag-service/                 # 业务逻辑层
│   └── rag-api/                     # 接口层 (启动入口, Controller)
└── docker/                          # Docker 配置
```

## Java 模块职责

### rag-common
- `Result.java` - 统一 API 返回格式
- `JwtUtils.java` - JWT 令牌生成与解析
- `CurrentUser.java` - ThreadLocal 存储当前登录用户 ID
- `GlobalExceptionHandler.java` - 全局异常捕获
- `MinioConfig.java` - MinIO 配置

### rag-domain
- `entity/` - 数据库表映射 (User, Document, QaSession, QaHistory)
- `dto/` - 数据传输对象 (LoginRequestDTO, AskRequest 等)
- `mapper/` - MyBatis Mapper 接口 + XML SQL

### rag-service
- `UserServiceImpl` - 用户登录/注册/信息管理 (验证码存 Redis)
- `DocumentServiceImpl` - 文档上传/删除，触发 Python 向量化
- `QaServiceImpl` - 问答核心逻辑，调用 Python AI 服务
- `FileServiceImpl` - MinIO 文件存储操作
- `AutoFillAspect` - 自动填充 create_time/update_time

### rag-api
- `RagKnowledgeSystemApplication.java` - 启动类
- `UserController` / `DocumentController` / `QaController` - REST 接口
- `LoginInterceptor` - JWT 认证拦截器
- `WebMvcConfig` - 拦截器注册

---

## 数据库 (MySQL)

数据库名: `rag_knowledge`

| 表名 | 说明 |
|------|------|
| `user` | 用户 (phone 登录, username, email, role) |
| `document` | 文档 (file_name, file_path MinIO路径, status: 0已上传/1处理中/2完成/3失败) |
| `qa_session` | 问答会话 (title 为首条问题) |
| `qa_history` | 问答历史 (question, answer, sources JSON) |

## 外部依赖服务

- **MySQL**: localhost:3306 (root/1234, 数据库 rag_knowledge)
- **Redis**: localhost:6379 (密码: sty01725, 存验证码)
- **MinIO**: localhost:9000 (minioadmin/minioadmin, bucket: rag-knowledge)
- **Python AI 服务**: localhost:8000 (FastAPI, RAG 核心引擎)

---

## API 接口

### 用户 `/user`
- `POST /user/send-code` - 发送验证码 (无需认证)
- `POST /user/login` - 手机号+验证码登录，返回 JWT (无需认证)
- `GET /user/info?phone=xxx` - 获取用户信息
- `PUT /user/update` - 更新用户信息

### 文档 `/document`
- `POST /document/upload` - 上传文档 (multipart, 触发 Python 向量化)
- `GET /document/list` - 获取文档列表
- `GET /document/url?id=xxx` - 获取 MinIO 预览 URL
- `DELETE /document/delete?id=xxx` - 删除文档

### 问答 `/qa`
- `POST /qa/ask` - 提问 (调用 Python RAG 服务)
- `GET /qa/sessions` - 会话列表
- `POST /qa/session` - 创建新会话
- `GET /qa/history?sessionId=xxx` - 问答历史
- `DELETE /qa/session/{id}` - 删除会话
- `DELETE /qa/history/{id}` - 删除问答记录

---

## 开发指南

### 编译与运行
```bash
cd Rag-knowledge-system
mvn clean package -DskipTests
java -jar rag-api/target/rag-api-0.0.1-SNAPSHOT.jar
```

### 基础设施启动
```bash
docker-compose up -d   # 启动 Redis + MinIO
```

### 代码规范
- Java 25, Maven 构建
- MyBatis XML 映射 (非注解), 驼峰自动映射
- 统一返回 `Result<T>` 包装
- 异常用 `BizException` 抛出，全局捕获
- JWT 拦截器校验，`CurrentUser` 获取当前用户
- 文档上传后异步调用 Python 服务进行向量化

### 修改注意事项
- 修改 API 路径时需同步更新 Nginx 配置 (`D:\DOWNLOAD\nginx-query\conf\nginx.conf`)
- 修改数据库表结构需更新 `sql/` 目录脚本和对应 Entity/Mapper
- Python 服务调用地址在 `application.yml` 的 `ai-service.python-base-url`
