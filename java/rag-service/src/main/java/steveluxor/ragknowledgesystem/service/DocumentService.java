package steveluxor.ragknowledgesystem.service;

import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.Result;

public interface DocumentService {
    // 上传文档
    Result uploadDocument(MultipartFile file, Long userId, Integer permission);

    // 获取文档URL
    Result getDocumentUrl(Long documentId);

    // 删除文档
    Result deleteDocument(Long documentId, Long userId);

    // 获取文档列表
    Result listDocuments(Long userId);
}
