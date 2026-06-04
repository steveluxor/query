package steveluxor.ragknowledgesystem.mapper;

import org.apache.ibatis.annotations.Mapper;
import steveluxor.ragknowledgesystem.entity.User;

@Mapper
public interface UserMapper {
    // 根据手机号查询用户
    User selectByPhone(String phone);


    // 添加用户
    void addUser(User user);


    // 根据手机号更新用户信息
    void updateByPhone(User user);

    // 根据ID查询用户
    User selectById(Long id);

    // 根据ID删除用户
    void deleteById(Long id);
}
