package steveluxor.ragknowledgesystem.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import steveluxor.ragknowledgesystem.entity.QaHistory;

import java.util.List;

@Mapper
public interface QaHistoryMapper {
    void insert(QaHistory qaHistory);

    List<QaHistory> selectByUserId(Long userId);

    void deleteById(Long id);

    void deleteBatch(@Param("ids") List<Long> ids, @Param("userId") Long userId);

    List<QaHistory> selectBySessionId(@Param("sessionId") Long sessionId, @Param("userId") Long userId);

    void deleteBySessionId(@Param("sessionId") Long sessionId, @Param("userId") Long userId);
}
