package steveluxor.ragknowledgesystem.service;

import java.util.Collection;
import java.util.Map;

public interface DocumentSummaryCacheService {
    Map<Long, String> getSummaries(Collection<Long> documentIds);

    void saveSummary(Long documentId, String summary);

    void invalidate(Long documentId);
}
