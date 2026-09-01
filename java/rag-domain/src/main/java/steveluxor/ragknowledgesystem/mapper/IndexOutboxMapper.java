package steveluxor.ragknowledgesystem.mapper;

import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import steveluxor.ragknowledgesystem.entity.IndexOutboxEvent;

import java.util.List;

@Mapper
public interface IndexOutboxMapper {
    void insert(IndexOutboxEvent event);
    List<IndexOutboxEvent> selectDispatchable(@Param("limit") int limit);
    void markPublished(@Param("id") Long id);
    void markProcessing(@Param("eventId") String eventId);
    void markIndexed(@Param("eventId") String eventId);
    void markRetry(@Param("eventId") String eventId, @Param("error") String error);
    void recoverTimedOut(@Param("timeoutSeconds") int timeoutSeconds);
}
