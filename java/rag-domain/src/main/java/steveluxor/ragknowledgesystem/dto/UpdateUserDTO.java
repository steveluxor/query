package steveluxor.ragknowledgesystem.dto;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@AllArgsConstructor
@NoArgsConstructor
public class UpdateUserDTO {
    private Long id;
    private String username;
    private String password;
    private String email;
    private String role;
    private String phone;
}
