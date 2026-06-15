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
}
