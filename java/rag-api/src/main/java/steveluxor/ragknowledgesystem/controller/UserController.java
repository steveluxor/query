package steveluxor.ragknowledgesystem.controller;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;
import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.LoginRequestDTO;
import steveluxor.ragknowledgesystem.dto.SendCodeRequestDTO;
import steveluxor.ragknowledgesystem.dto.UpdateUserDTO;
import steveluxor.ragknowledgesystem.service.UserService;

@RestController
@RequestMapping("/user")
@Slf4j
public class UserController {
    private final UserService userService;
    @Autowired
    public UserController(UserService userService) {
        this.userService = userService;
    }
    @PostMapping("/send-code")
    public Result sendCode(@RequestBody SendCodeRequestDTO sendCodeRequestDTO) {
        return userService.sendCode(sendCodeRequestDTO);
    }

    /**
     * 用户登录
     * @param loginRequestDTO
     * @return
     */
    @PostMapping("/login")
    public Result login(@RequestBody LoginRequestDTO loginRequestDTO) {
        log.info("登录请求:{}",loginRequestDTO);
        return userService.login(loginRequestDTO);
    }

    /**
     * 获取用户信息
     * @param phone
     * @return
     */
    @GetMapping("/info")
    public Result info(@RequestParam String phone) {
        return userService.info(phone);
    }

    /**
     * 用户更新
     */
    @PutMapping("/update")
    public Result update(@RequestBody UpdateUserDTO updateUserDTO) {
        log.info("更新请求:{}",updateUserDTO);
        return userService.update(updateUserDTO);
    }
}
