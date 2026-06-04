package steveluxor.ragknowledgesystem.entity;

import io.netty.util.Constant;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.EqualsAndHashCode;
import lombok.NoArgsConstructor;
import steveluxor.ragknowledgesystem.common.Constants;

@Data
@EqualsAndHashCode(callSuper = true)
@AllArgsConstructor
@NoArgsConstructor
public class User extends BaseEntity {

    private Long id;
    private String username;
    private String password;
    private String email;
    private String role;
    private String phone;

    public User(String phone) {
        this.phone = phone;
        this.role = Constants.ROLE_USER;
    }
}
