package steveluxor.ragknowledgesystem.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import steveluxor.ragknowledgesystem.entity.QaSession;

import java.util.List;

@Mapper
public interface QaSessionMapper {

    void insert(QaSession session);

    List<QaSession> selectByUserId(Long userId);

    QaSession selectById(Long id);

    void updateTitle(@Param("id") Long id, @Param("title") String title);

    void updatePreferences(@Param("id") Long id, @Param("preferences") String preferences);

    void deleteById(Long id);

    void deleteByUserId(Long userId);
}
