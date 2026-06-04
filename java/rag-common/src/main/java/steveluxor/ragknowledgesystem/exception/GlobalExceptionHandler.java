package steveluxor.ragknowledgesystem.exception;

import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

import steveluxor.ragknowledgesystem.common.Result;

import static steveluxor.ragknowledgesystem.common.Constants.SERVER_ERROR;

@Slf4j
@RestControllerAdvice
public class GlobalExceptionHandler {

    @ExceptionHandler(BizException.class)
    public Result<Void> handleBizException(BizException e) {
        return Result.fail(e.getCode(), e.getMessage());
    }

    @ExceptionHandler(Exception.class)
    public Result<Void> handleException(Exception e) {
        log.error(SERVER_ERROR, e);
        return Result.fail(SERVER_ERROR);
    }
}
