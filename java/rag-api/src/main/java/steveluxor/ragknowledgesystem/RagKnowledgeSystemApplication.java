package steveluxor.ragknowledgesystem;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
@MapperScan("steveluxor.ragknowledgesystem.mapper")
public class RagKnowledgeSystemApplication {

    public static void main(String[] args) {
        SpringApplication.run(RagKnowledgeSystemApplication.class, args);
    }

}
