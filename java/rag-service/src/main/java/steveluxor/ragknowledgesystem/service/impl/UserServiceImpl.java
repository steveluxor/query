package steveluxor.ragknowledgesystem.service.impl;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import steveluxor.ragknowledgesystem.common.JwtUtils;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.exception.BizException;
import steveluxor.ragknowledgesystem.dto.LoginRequestDTO;
import steveluxor.ragknowledgesystem.dto.SendCodeRequestDTO;
import steveluxor.ragknowledgesystem.dto.UpdateUserDTO;
import steveluxor.ragknowledgesystem.entity.Document;
import steveluxor.ragknowledgesystem.entity.User;
import steveluxor.ragknowledgesystem.mapper.DocumentMapper;
import steveluxor.ragknowledgesystem.mapper.QaHistoryMapper;
import steveluxor.ragknowledgesystem.mapper.QaSessionMapper;
import steveluxor.ragknowledgesystem.mapper.UserMapper;
import steveluxor.ragknowledgesystem.service.FileService;
import steveluxor.ragknowledgesystem.service.UserService;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.List;

import static steveluxor.ragknowledgesystem.common.Constants.*;

@Service
@Slf4j
public class UserServiceImpl implements UserService {
    private final StringRedisTemplate stringRedisTemplate;
    private final UserMapper userMapper;
    private final JwtUtils jwtUtils;
    private final DocumentMapper documentMapper;
    private final QaHistoryMapper qaHistoryMapper;
    private final QaSessionMapper qaSessionMapper;
    private final FileService fileService;
    private final HttpClient httpClient;
    private final String pythonBaseUrl;

    @Autowired
    public UserServiceImpl(StringRedisTemplate stringRedisTemplate, UserMapper userMapper, JwtUtils jwtUtils,
                           DocumentMapper documentMapper, QaHistoryMapper qaHistoryMapper,
                           QaSessionMapper qaSessionMapper, FileService fileService,
                           @org.springframework.beans.factory.annotation.Value("${ai-service.python-base-url:http://localhost:8000}") String pythonBaseUrl) {
        this.stringRedisTemplate = stringRedisTemplate;
        this.userMapper = userMapper;
        this.jwtUtils = jwtUtils;
        this.documentMapper = documentMapper;
        this.qaHistoryMapper = qaHistoryMapper;
        this.qaSessionMapper = qaSessionMapper;
        this.fileService = fileService;
        this.pythonBaseUrl = pythonBaseUrl;
        this.httpClient = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .version(HttpClient.Version.HTTP_1_1)
                .build();
    }

    /**
     * 发送验证码
     * @param sendCodeRequestDTO
     * @return
     */
    @Override
    public Result sendCode(SendCodeRequestDTO sendCodeRequestDTO) {
        String phone= sendCodeRequestDTO.getPhone();
        log.info("发送验证码请求:{}",phone);
        //如果手机号符合规则,发送验证码
        if (!phone.matches("^1[3456789]\\d{9}$")) {
            log.error("手机号格式错误:{}",phone);
            throw new BizException(PHONE_FORMAT_ERROR);
        }
        //使用ThreadLocalRandom随机数创建一个6位验证码
        String code= String.valueOf(ThreadLocalRandom.current().nextInt(100000,999999+1));
        log.info("发送验证码:{}",code);
        //将验证码存储到Redis中,过期时间为2分钟
        stringRedisTemplate.opsForValue().set(CODE_PREFIX+phone,code,2, TimeUnit.MINUTES);
        //如果验证码发送成功,返回成功
        return Result.ok(CODE_SEND_SUCCESS);
    }


    /**
     * 登录
     * @param loginRequestDTO
     * @return
     */
    @Override
    public Result login(LoginRequestDTO loginRequestDTO) {
        String phone= loginRequestDTO.getPhone();
        String code= loginRequestDTO.getCode();
        //如果手机号符合规则,发送验证码
        if (!phone.matches("^1[3456789]\\d{9}$")) {
            log.error("手机号格式错误:{}",phone);
            throw new BizException(PHONE_FORMAT_ERROR);
        }
        //如果验证码符合规则,登录成功
        if (!code.equals(stringRedisTemplate.opsForValue().get(CODE_PREFIX+phone))) {
            throw new BizException(CODE_ERROR);
        }
        //根据手机号查询用户
        User user = userMapper.selectByPhone(phone);
        //如果用户不存在,添加用户
        if (user == null) {
            userMapper.addUser(new User(phone));
            user = userMapper.selectByPhone(phone); // 重新查询以获取完整信息（含自增ID）
        }
        log.info("登录成功:{}", phone);
        // 生成 JWT 令牌
        String token = jwtUtils.generateToken(user.getId(), user.getPhone(), user.getRole());
        Map<String, Object> data = new HashMap<>();
        data.put("token", token);
        data.put("user", user);
        return Result.ok(data);
    }

    /**
     * 获取用户信息
     * @param phone
     * @return
     */
    @Override
    public Result info(String phone) {
        User user = userMapper.selectByPhone(phone);
        if (user == null) {
            throw new BizException(USER_NOT_EXIST);
        }
        return Result.ok(user);
    }

    /**
     * 更新用户信息
     * @param updateUserDTO
     * @return
     */
    @Override
    public Result update(UpdateUserDTO updateUserDTO) {
        User user = userMapper.selectByPhone(updateUserDTO.getPhone());
        if (user == null) {
            throw new BizException(USER_NOT_EXIST);
        }
        // 拷贝需要更新的字段
        if (updateUserDTO.getUsername() != null) {
            user.setUsername(updateUserDTO.getUsername());
        }
        if (updateUserDTO.getEmail() != null) {
            user.setEmail(updateUserDTO.getEmail());
        }
        if (updateUserDTO.getPassword() != null) {
            user.setPassword(updateUserDTO.getPassword());
        }
        //根据手机号更新用户信息
        userMapper.updateByPhone(user);
        return Result.ok(USER_UPDATE_SUCCESS);
    }

    /**
     * 删除用户及其所有关联数据（文档、对话历史、会话）
     */
    @Transactional(rollbackFor = Exception.class)
    @Override
    public Result delete(Long userId) {
        User user = userMapper.selectById(userId);
        if (user == null) {
            throw new BizException(USER_NOT_EXIST);
        }

        // 1. 删除用户的文档（MinIO文件 + 向量库 + 数据库记录）
        List<Document> documents = documentMapper.selectOwnByUserId(userId);
        for (Document doc : documents) {
            try {
                // 删除向量库中的文档切片
                try {
                    HttpRequest delReq = HttpRequest.newBuilder()
                            .uri(URI.create(pythonBaseUrl + "/ingest/document/" + doc.getId()))
                            .timeout(Duration.ofSeconds(30))
                            .DELETE()
                            .build();
                    httpClient.send(delReq, HttpResponse.BodyHandlers.ofString());
                } catch (Exception e) {
                    log.warn("向量库文档删除失败（不影响后续删除）: documentId={}", doc.getId(), e);
                }
                // 删除MinIO文件
                fileService.deleteFile(doc.getFilePath());
            } catch (Exception e) {
                log.warn("文件删除失败（不影响后续删除）: documentId={}", doc.getId(), e);
            }
        }
        // 批量删除文档数据库记录
        documentMapper.deleteByUserId(userId);

        // 2. 删除对话历史
        qaHistoryMapper.deleteByUserId(userId);

        // 3. 删除会话
        qaSessionMapper.deleteByUserId(userId);

        // 4. 删除用户
        userMapper.deleteById(userId);

        log.info("用户删除成功: userId={}, phone={}", userId, user.getPhone());
        return Result.ok(USER_DELETE_SUCCESS);
    }
}
