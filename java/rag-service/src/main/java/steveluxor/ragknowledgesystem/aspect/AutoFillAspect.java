package steveluxor.ragknowledgesystem.aspect;

import org.aspectj.lang.JoinPoint;
import org.aspectj.lang.annotation.Aspect;
import org.aspectj.lang.annotation.Before;
import org.springframework.stereotype.Component;
import steveluxor.ragknowledgesystem.common.CurrentUser;
import steveluxor.ragknowledgesystem.entity.BaseEntity;

import java.time.LocalDateTime;

@Aspect
@Component
public class AutoFillAspect {

    @Before("execution(* steveluxor.ragknowledgesystem.mapper.*.insert*(..))")
    public void autoFillInsert(JoinPoint joinPoint) {
        for (Object arg : joinPoint.getArgs()) {
            if (arg instanceof BaseEntity entity) {
                LocalDateTime now = LocalDateTime.now();
                Long userId = CurrentUser.get();
                entity.setCreateTime(now);
                entity.setCreateUser(userId);
                entity.setUpdateTime(now);
                entity.setUpdateUser(userId);
            }
        }
    }

    @Before("execution(* steveluxor.ragknowledgesystem.mapper.*.update*(..))")
    public void autoFillUpdate(JoinPoint joinPoint) {
        for (Object arg : joinPoint.getArgs()) {
            if (arg instanceof BaseEntity entity) {
                entity.setUpdateTime(LocalDateTime.now());
                entity.setUpdateUser(CurrentUser.get());
            }
        }
    }
}
