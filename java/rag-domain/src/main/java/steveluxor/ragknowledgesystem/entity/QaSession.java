package steveluxor.ragknowledgesystem.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.experimental.SuperBuilder;

@Data
@EqualsAndHashCode(callSuper = true)
@AllArgsConstructor
@NoArgsConstructor
@SuperBuilder
public class QaSession extends BaseEntity {

    private Long id;
    private Long userId;
    private String title;
    private String preferences;  // JSON 字符串，存储用户偏好
}
