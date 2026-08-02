import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    """Runtime 事件类型 — 双层分离：Workflow Layer + Generation Layer"""
    # Workflow Layer（低频，状态变化）
    RUNTIME_STARTED = "runtime_started"
    PLAN_GENERATED = "plan_generated"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    CONTROL_ACTION = "control_action"      # Critic retry 等控制信号
    RUNTIME_COMPLETED = "runtime_completed"
    RUNTIME_ERROR = "runtime_error"

    # Generation Layer（高频，token 增量）
    TOKEN_CHUNK = "token_chunk"


@dataclass
class RuntimeEvent:
    """单个 Runtime 事件"""
    run_id: str
    type: EventType
    data: dict = field(default_factory=dict)


class RuntimeEventBus:
    """per-run pub-sub 事件总线

    设计要点：
    - 每个 run_id 支持多个 subscriber（runtime SSE + answer SSE 独立消费）
    - 每个 subscriber 有独立的 asyncio.Queue（pub-sub，非竞争消费）
    - 预创建 channel：/qa/ask 时先 create_channel，再启动任务，避免事件丢失
    - 无状态：run 结束后 cleanup，不持久化事件
    """

    def __init__(self):
        self._channels: dict[str, list[asyncio.Queue]] = {}
        self._history: dict[str, list[RuntimeEvent]] = {}

    def create_channel(self, run_id: str):
        """预创建 channel，/qa/ask 中在 create_task 之前调用"""
        if run_id not in self._channels:
            self._channels[run_id] = []
            self._history[run_id] = []
            logger.debug("[EventBus] channel created: %s", run_id)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """订阅 run_id 的事件流，返回独立 Queue（包含历史事件）"""
        q: asyncio.Queue = asyncio.Queue()
        # 先放入历史事件，再加入 subscriber 列表
        for event in self._history.get(run_id, []):
            q.put_nowait(event)
        self._channels.setdefault(run_id, []).append(q)
        logger.debug("[EventBus] subscriber added: %s (history=%d, total=%d)",
                     run_id, len(self._history.get(run_id, [])), len(self._channels[run_id]))
        return q

    async def publish(self, event: RuntimeEvent):
        """发布事件到所有 subscriber（fan-out）+ 缓存历史"""
        # 缓存事件（供迟到的 subscriber 回放）
        if event.run_id in self._history:
            self._history[event.run_id].append(event)
        subscribers = self._channels.get(event.run_id, [])
        for q in subscribers:
            await q.put(event)
        if subscribers:
            logger.debug("[EventBus] published %s to %d subscribers: %s",
                         event.type.value, len(subscribers), event.run_id)

    def cleanup(self, run_id: str):
        """清理 run_id 的所有 subscriber 和历史"""
        self._channels.pop(run_id, None)
        self._history.pop(run_id, None)
        logger.debug("[EventBus] cleanup: %s", run_id)

    @property
    def active_runs(self) -> int:
        return len(self._channels)
