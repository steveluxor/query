package steveluxor.ragknowledgesystem.entity;

import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.AllArgsConstructor;
import lombok.NoArgsConstructor;
import lombok.experimental.SuperBuilder;

@Data
@EqualsAndHashCode(callSuper = true)
@AllArgsConstructor
@NoArgsConstructor
@SuperBuilder
public class QaHistory extends BaseEntity {

    private Long id;
    private Long userId;
    private String question;
    private String answer;
    private String sources;
    private Long sessionId;
    private Boolean isAgg;
    private String imageUrls;  // MinIO objectName 列表(JSON)
    private String plan;           // Agent 执行计划(DAG JSON)
    private String agentTrace;     // Agent 执行轨迹(JSON)
    private String generatedCode;  // CodeAgent 生成的代码
    private String codeStdout;     // 代码执行标准输出
    private String codeError;      // 代码执行错误信息
    private Boolean codeSuccess;   // 代码执行是否成功
}
