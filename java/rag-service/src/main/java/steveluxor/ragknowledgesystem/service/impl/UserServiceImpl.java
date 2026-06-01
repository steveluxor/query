package steveluxor.ragknowledgesystem.service.impl;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import steveluxor.ragknowledgesystem.common.JwtUtils;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.LoginRequestDTO;
import steveluxor.ragknowledgesystem.dto.SendCodeRequestDTO;
import steveluxor.ragknowledgesystem.dto.UpdateUserDTO;
import steveluxor.ragknowledgesystem.entity.User;
import steveluxor.ragknowledgesystem.mapper.UserMapper;
import steveluxor.ragknowledgesystem.service.UserService;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ThreadLocalRandom;
import java.util.concurrent.TimeUnit;

import static steveluxor.ragknowledgesystem.common.Constants.CODE_PREFIX;

@Service
@Slf4j
public class UserServiceImpl implements UserService {
    private final StringRedisTemplate stringRedisTemplate;
    private final UserMapper userMapper;
    private final JwtUtils jwtUtils;

    @Autowired
    public UserServiceImpl(StringRedisTemplate stringRedisTemplate, UserMapper userMapper, JwtUtils jwtUtils) {
        this.stringRedisTemplate = stringRedisTemplate;
        this.userMapper = userMapper;
        this.jwtUtils = jwtUtils;
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
            return Result.fail("手机号格式错误");
        }
        //使用ThreadLocalRandom随机数创建一个6位验证码
        String code= String.valueOf(ThreadLocalRandom.current().nextInt(100000,999999+1));
        log.info("发送验证码:{}",code);
        //将验证码存储到Redis中,过期时间为2分钟
        stringRedisTemplate.opsForValue().set(CODE_PREFIX+phone,code,2, TimeUnit.MINUTES);
        //如果验证码发送成功,返回成功
        return Result.ok("验证码发送成功");
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
            return Result.fail("手机号格式错误");
        }
        //如果验证码符合规则,登录成功
        if (!code.equals(stringRedisTemplate.opsForValue().get(CODE_PREFIX+phone))) {
            return Result.fail("验证码错误");
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
            return Result.fail("用户不存在");
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
            return Result.fail("用户不存在");
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
        return Result.ok("更新成功");
    }
}
