package steveluxor.ragknowledgesystem.common.config;

import org.springframework.amqp.core.Binding;
import org.springframework.amqp.core.BindingBuilder;
import org.springframework.amqp.core.DirectExchange;
import org.springframework.amqp.core.Queue;
import org.springframework.amqp.support.converter.JacksonJsonMessageConverter;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import static steveluxor.ragknowledgesystem.common.Constants.*;

@Configuration
public class RabbitMQConfig {

    @Bean
    public JacksonJsonMessageConverter jacksonMessageConverter() {
        return new JacksonJsonMessageConverter();
    }

    @Bean
    public DirectExchange ingestExchange() {
        return new DirectExchange(RABBITMQ_INGEST_EXCHANGE, true, false);
    }

    @Bean
    public Queue ingestQueue() {
        return new Queue(RABBITMQ_INGEST_QUEUE, true);
    }

    @Bean
    public Binding ingestBinding(DirectExchange ingestExchange, Queue ingestQueue) {
        return BindingBuilder.bind(ingestQueue)
                .to(ingestExchange)
                .with(RABBITMQ_INGEST_ROUTING_KEY);
    }
}
