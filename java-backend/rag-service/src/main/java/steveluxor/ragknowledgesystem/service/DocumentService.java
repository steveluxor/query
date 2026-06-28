package steveluxor.ragknowledgesystem.service;

import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.Result;

public interface DocumentService {
    // 上传文档
    Result uploadDocument(MultipartFile file, Long userId, Integer permission);

    // 删除文档
    Result deleteDocument(Long documentId, Long userId) throws Exception;

    // 获取文档列表
    Result listDocuments(Long userId);

    // 重新向量化
    Result reIngestDocument(Long documentId, Long userId);

    // 检查文件名是否重复
    Result checkDuplicate(String fileName, Long userId);

    // 覆盖上传文档
    Result overwriteDocument(Long oldDocId, MultipartFile file, Long userId, Integer permission);

    // 更新文档状态（供 Python 消费者调用）
    void updateDocumentStatus(Long documentId, String status);
}
