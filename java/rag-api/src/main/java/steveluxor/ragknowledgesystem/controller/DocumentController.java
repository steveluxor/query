package steveluxor.ragknowledgesystem.controller;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.service.DocumentService;

@RestController
@RequestMapping("/document")
@Slf4j
public class DocumentController {
    private final DocumentService documentService;

    @Autowired
    public DocumentController(DocumentService documentService) {
        this.documentService = documentService;
    }

    @PostMapping("/upload")
    public Result uploadDocument(@RequestParam("file") MultipartFile file,
                                 @RequestParam("userId") Long userId,
                                 @RequestParam Integer permission) {
        log.info("上传文档请求: fileName={}, userId={}, permission={}", file.getOriginalFilename(), userId, permission);
        return documentService.uploadDocument(file, userId, permission);
    }

    @GetMapping("/{id}/url")
    public Result getDocumentUrl(@PathVariable("id") Long documentId) {
        log.info("获取文档URL请求: documentId={}", documentId);
        return documentService.getDocumentUrl(documentId);
    }

    @GetMapping("/list")
    public Result listDocuments(@RequestParam("userId") Long userId) {
        log.info("查询文档列表请求: userId={}", userId);
        return documentService.listDocuments(userId);
    }

    @DeleteMapping("/{id}")
    public Result deleteDocument(@PathVariable("id") Long documentId,
                                 @RequestParam("userId") Long userId) {
        log.info("删除文档请求: documentId={}, userId={}", documentId, userId);
        return documentService.deleteDocument(documentId, userId);
    }
}
