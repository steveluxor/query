package steveluxor.ragknowledgesystem.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class IndexOutboxEvent {
    private Long id;
    private String eventId;
    private Long documentId;
    private Integer indexVersion;
    private String eventType;
    private String payload;
    private String status;
    private Integer retryCount;
    private LocalDateTime nextRetryTime;
    private String lastError;
    private LocalDateTime publishedTime;
}
