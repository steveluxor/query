package steveluxor.ragknowledgesystem;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
@MapperScan("steveluxor.ragknowledgesystem.mapper")
public class RagKnowledgeSystemApplication {

    public static void main(String[] args) {
        SpringApplication.run(RagKnowledgeSystemApplication.class, args);
    }

}
