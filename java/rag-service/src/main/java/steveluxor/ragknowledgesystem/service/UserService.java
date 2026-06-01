package steveluxor.ragknowledgesystem.service;

import steveluxor.ragknowledgesystem.common.Result;
import steveluxor.ragknowledgesystem.dto.LoginRequestDTO;
import steveluxor.ragknowledgesystem.dto.SendCodeRequestDTO;
import steveluxor.ragknowledgesystem.dto.UpdateUserDTO;

public interface UserService {
    Result sendCode(SendCodeRequestDTO sendCodeRequestDTO);

    Result login(LoginRequestDTO loginRequestDTO);


    Result info(String phone);

    Result update(UpdateUserDTO updateUserDTO);
}
