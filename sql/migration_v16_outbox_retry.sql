-- Apply once after migration_v15_index_outbox.sql on an existing MySQL volume.
ALTER TABLE index_outbox
    ADD COLUMN next_retry_time DATETIME DEFAULT NULL,
    ADD COLUMN last_error VARCHAR(1000) DEFAULT NULL;
