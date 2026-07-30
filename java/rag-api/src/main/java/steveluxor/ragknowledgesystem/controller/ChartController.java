package steveluxor.ragknowledgesystem.controller;

import io.minio.GetObjectArgs;
import io.minio.MinioClient;
import jakarta.servlet.http.HttpServletResponse;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.web.bind.annotation.*;

import java.io.InputStream;

@RestController
@Slf4j
public class ChartController {

    private final MinioClient minioClient;
    private final String bucketName;

    @Autowired
    public ChartController(MinioClient minioClient,
                           @Value("${minio.bucket-name}") String bucketName) {
        this.minioClient = minioClient;
        this.bucketName = bucketName;
    }

    @GetMapping("/charts/{objectName}")
    public void getChart(@PathVariable String objectName, HttpServletResponse response) {
        // objectName 可能是 "xxx.png" 或 "charts/xxx.png"，统一处理
        String objectPath = objectName.startsWith("charts/") ? objectName : "charts/" + objectName;
        try (InputStream is = minioClient.getObject(GetObjectArgs.builder()
                .bucket(bucketName)
                .object(objectPath)
                .build())) {
            response.setContentType("image/png");
            response.setHeader("Cache-Control", "max-age=86400");
            is.transferTo(response.getOutputStream());
            response.getOutputStream().flush();
        } catch (Exception e) {
            log.warn("获取图表失败: {}", objectName, e);
            response.setStatus(404);
        }
    }
}
