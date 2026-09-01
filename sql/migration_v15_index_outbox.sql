-- Apply once to an existing mysql-data volume before deploying v15.
-- MySQL 8.0.40 does not accept ADD COLUMN IF NOT EXISTS. Run this file once.
ALTER TABLE document
    ADD COLUMN index_version INT NOT NULL DEFAULT 1 COMMENT '待构建的索引版本',
    ADD COLUMN active_index_version INT DEFAULT NULL COMMENT '当前可查询的索引版本';

UPDATE document
SET index_version = 1,
    active_index_version = CASE WHEN status = 'COMPLETED' THEN 1 ELSE NULL END
WHERE active_index_version IS NULL;

CREATE TABLE IF NOT EXISTS index_outbox (
    id BIGINT PRIMARY KEY AUTO_INCREMENT,
    event_id CHAR(36) NOT NULL,
    document_id BIGINT NOT NULL,
    index_version INT NOT NULL,
    event_type VARCHAR(20) NOT NULL,
    payload JSON NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    retry_count INT NOT NULL DEFAULT 0,
    published_time DATETIME DEFAULT NULL,
    create_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    update_time DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_index_outbox_event_id (event_id),
    KEY idx_index_outbox_pending (status, id)
);
