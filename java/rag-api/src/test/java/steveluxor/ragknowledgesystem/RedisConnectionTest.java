package steveluxor.ragknowledgesystem;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.data.redis.core.StringRedisTemplate;

import static org.junit.jupiter.api.Assertions.assertEquals;

@SpringBootTest
class RedisConnectionTest {

    @Autowired
    private StringRedisTemplate redisTemplate;

    @Test
    void testRedisConnection() {
        redisTemplate.opsForValue().set("test:hello", "Redis connected!");
        String value = redisTemplate.opsForValue().get("test:hello");
        assertEquals("Redis connected!", value);
        redisTemplate.delete("test:hello");
    }
}
