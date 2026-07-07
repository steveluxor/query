package steveluxor.ragknowledgesystem.entity;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import lombok.experimental.SuperBuilder;

@Data
@EqualsAndHashCode(callSuper = true)// 调用父类的equals方法
@AllArgsConstructor
@NoArgsConstructor
@SuperBuilder
public class Document extends BaseEntity {

    private Long id;
    private Long userId;
    private String fileName;
    private String filePath;
    private Long fileSize;
    private String fileType;
    private String status;
    private Integer permission;
}
