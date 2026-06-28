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
    create_time DATETIME,
    update_time DATETIME,
    create_user BIGINT,
    update_user BIGINT
);

CREATE TABLE IF NOT EXISTS qa_session (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    user_id BIGINT NOT NULL,
    title VARCHAR(200) COMMENT '会话标题（取第一条问题）',
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
