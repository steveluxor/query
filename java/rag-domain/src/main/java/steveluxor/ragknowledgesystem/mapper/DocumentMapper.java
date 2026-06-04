package steveluxor.ragknowledgesystem.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import steveluxor.ragknowledgesystem.entity.Document;

import java.util.List;

@Mapper
public interface DocumentMapper {
    // 插入文档
    void insert(Document document);

    // 根据ID查询文档
    Document selectById(Long id);

    // 删除文档
    void deleteById(Long id);

    // 根据用户ID查询文档列表（含公开文档）
    List<Document> selectByUserId(Long userId);

    // 仅查询用户自己的文档（不含公开文档）
    List<Document> selectOwnByUserId(Long userId);

    // 更新文档状态
    void updateStatus(@Param("id") Long id, @Param("status") String status);

    // 根据用户ID批量删除文档
    void deleteByUserId(Long userId);
}
