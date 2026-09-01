CREATE DATABASE IF NOT EXISTS rag_knowledge DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE rag_knowledge;

CREATE TABLE IF NOT EXISTS user (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(100),
    password VARCHAR(255),
    email VARCHAR(200),
    role VARCHAR(50),
    phone VARCHAR(20),
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);

CREATE TABLE IF NOT EXISTS document (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT,
    file_name VARCHAR(500),
    file_path VARCHAR(1000),
    file_size BIGINT,
    file_type VARCHAR(100),
    status VARCHAR(50),
    permission INT,
    summary TEXT COMMENT '文档摘要（Python 生成）',
    index_version INT NOT NULL DEFAULT 1 COMMENT '待构建的索引版本',
    active_index_version INT DEFAULT NULL COMMENT '当前可查询的索引版本',
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);

-- Outbox 与 document 状态在同一 MySQL 事务提交；发布器可在 RabbitMQ 暂不可用时重试。
CREATE TABLE IF NOT EXISTS index_outbox (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    event_id CHAR(36) NOT NULL,
    document_id BIGINT NOT NULL,
    index_version INT NOT NULL,
    event_type VARCHAR(20) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    retry_count INT NOT NULL DEFAULT 0,
    next_retry_time DATETIME DEFAULT NULL,
    last_error VARCHAR(1000) DEFAULT NULL,
    published_time DATETIME DEFAULT NULL,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_index_outbox_event_id (event_id),
    KEY idx_index_outbox_pending (status, id)
);

CREATE TABLE IF NOT EXISTS qa_session (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    title VARCHAR(200) COMMENT '会话标题（取第一条问题）',
    preferences JSON DEFAULT NULL COMMENT '用户偏好（JSON），由 Python AgentMemory 提取后回写',
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);

CREATE TABLE IF NOT EXISTS qa_history (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT,
    question TEXT,
    answer TEXT,
    sources TEXT,
    session_id BIGINT,
    is_agg TINYINT(1) DEFAULT 0 COMMENT '本次回答是否使用聚合查询',
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);

-- session_id 列已在 CREATE TABLE 中定义，无需 ALTER
