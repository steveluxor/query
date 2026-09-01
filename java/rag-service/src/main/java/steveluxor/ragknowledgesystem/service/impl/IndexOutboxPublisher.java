package steveluxor.ragknowledgesystem.service.impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.extern.slf4j.Slf4j;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.amqp.rabbit.connection.CorrelationData;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import steveluxor.ragknowledgesystem.entity.IndexOutboxEvent;
import steveluxor.ragknowledgesystem.mapper.IndexOutboxMapper;

import java.util.Map;
import java.util.concurrent.TimeUnit;

import static steveluxor.ragknowledgesystem.common.Constants.RABBITMQ_INGEST_EXCHANGE;
import static steveluxor.ragknowledgesystem.common.Constants.RABBITMQ_INGEST_ROUTING_KEY;

/** Publishes index events and recovers producer/consumer timeouts. */
@Component
@Slf4j
public class IndexOutboxPublisher {
    private final IndexOutboxMapper outboxMapper;
    private final RabbitTemplate rabbitTemplate;
    private final ObjectMapper objectMapper;

    public IndexOutboxPublisher(IndexOutboxMapper outboxMapper, RabbitTemplate rabbitTemplate, ObjectMapper objectMapper) {
        this.outboxMapper = outboxMapper;
        this.rabbitTemplate = rabbitTemplate;
        this.objectMapper = objectMapper;
    }

    @Scheduled(fixedDelayString = "${index-outbox.publish-interval-ms:1000}")
    public void publishPending() {
        outboxMapper.recoverTimedOut(300);
        for (IndexOutboxEvent event : outboxMapper.selectDispatchable(50)) {
            try {
                Map<String, Object> payload = objectMapper.readValue(event.getPayload(), new TypeReference<>() {});
                CorrelationData correlation = new CorrelationData(event.getEventId());
                rabbitTemplate.convertAndSend(RABBITMQ_INGEST_EXCHANGE, RABBITMQ_INGEST_ROUTING_KEY, payload, correlation);
                CorrelationData.Confirm confirm = correlation.getFuture().get(5, TimeUnit.SECONDS);
                if (!confirm.isAck()) {
                    throw new IllegalStateException("RabbitMQ 拒绝消息: " + confirm.getReason());
                }
                outboxMapper.markPublished(event.getId());
                log.info("索引 Outbox 已发布: eventId={}, documentId={}, version={}, type={}",
                        event.getEventId(), event.getDocumentId(), event.getIndexVersion(), event.getEventType());
            } catch (Exception e) {
                outboxMapper.markRetry(event.getEventId(), safeError(e));
                log.warn("索引 Outbox 发布失败，将重试: eventId={}, retry={}", event.getEventId(), event.getRetryCount(), e);
            }
        }
    }

    private static String safeError(Exception error) {
        String message = String.valueOf(error.getMessage());
        return message.length() > 900 ? message.substring(0, 900) : message;
    }
}
