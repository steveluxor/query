"""Redis-backed checkpoint storage for resumable Agent runs."""
from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import time
from enum import Enum
from typing import Any

from app.config import settings
from app.core.agent_context import AgentContext
from app.models.data_types import AgentOutput
from app.models.task_graph import TaskGraph, TaskNode, TaskStatus

logger = logging.getLogger(__name__)


class RunStateStore:
    """Persist the execution state needed to continue a run after a restart.

    Redis is the source of truth while a run is active.  Large artifacts should
    be represented by object-storage references before this store is used for
    them; the serializer intentionally only accepts JSON-compatible values and
    project dataclasses.
    """

    PREFIX = "qa:run"
    EVENT_MAXLEN = 2000

    def __init__(self, client):
        self.client = client

    @classmethod
    def meta_key(cls, run_id: str) -> str:
        return f"{cls.PREFIX}:{run_id}:meta"

    @classmethod
    def tasks_key(cls, run_id: str) -> str:
        return f"{cls.PREFIX}:{run_id}:tasks"

    @classmethod
    def events_key(cls, run_id: str) -> str:
        return f"{cls.PREFIX}:{run_id}:events"

    @classmethod
    def lease_key(cls, run_id: str) -> str:
        return f"{cls.PREFIX}:{run_id}:lease"

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _history_item(item: Any) -> dict:
        if isinstance(item, dict):
            return dict(item)
        return {
            "question": getattr(item, "question", ""),
            "answer": getattr(item, "answer", ""),
            "is_agg": getattr(item, "is_agg", False),
        }

    @staticmethod
    def _encode_value(value: Any) -> Any:
        if dataclasses.is_dataclass(value):
            return {
                "__type__": f"{value.__class__.__module__}:{value.__class__.__qualname__}",
                # asdict() would erase nested dataclass type information (for
                # example DocumentBundle -> DocumentChunk), making resumed
                # downstream Agents receive plain dicts instead of models.
                "data": {
                    field.name: RunStateStore._encode_value(getattr(value, field.name))
                    for field in dataclasses.fields(value)
                },
            }
        if isinstance(value, Enum):
            return {"__enum__": f"{value.__class__.__module__}:{value.__class__.__qualname__}", "value": value.value}
        if isinstance(value, dict):
            return {str(key): RunStateStore._encode_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [RunStateStore._encode_value(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise TypeError(f"运行状态不支持序列化类型: {type(value).__name__}")

    @staticmethod
    def _resolve_type(path: str):
        module_name, class_name = path.split(":", 1)
        target = importlib.import_module(module_name)
        for part in class_name.split("."):
            target = getattr(target, part)
        return target

    @staticmethod
    def _decode_value(value: Any) -> Any:
        if isinstance(value, list):
            return [RunStateStore._decode_value(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "__enum__" in value:
            return RunStateStore._resolve_type(value["__enum__"])(value["value"])
        if "__type__" in value:
            target = RunStateStore._resolve_type(value["__type__"])
            return target(**RunStateStore._decode_value(value["data"]))
        return {key: RunStateStore._decode_value(item) for key, item in value.items()}

    @staticmethod
    def _serialize_plan(plan: TaskGraph) -> dict:
        return {
            "goal": plan.goal,
            "goal_outputs": plan.goal_outputs,
            "tasks": [
                {
                    "id": task.id,
                    "agent": task.agent,
                    "objective": task.objective,
                    "depends_on": task.depends_on,
                    "output_key": task.output_key,
                    "port_bindings": task.port_bindings,
                    "status": task.status.value,
                    "duration_ms": task.duration_ms,
                    "summary": task.summary,
                    "tools_used": task.tools_used,
                    "artifacts": task.artifacts,
                }
                for task in plan.tasks
            ],
        }

    @staticmethod
    def _deserialize_plan(data: dict) -> TaskGraph:
        return TaskGraph(
            goal=data.get("goal", ""),
            goal_outputs=data.get("goal_outputs", []),
            tasks=[
                TaskNode(
                    id=item["id"], agent=item["agent"], objective=item.get("objective", ""),
                    depends_on=item.get("depends_on", []), output_key=item.get("output_key", ""),
                    port_bindings=item.get("port_bindings", {}),
                    status=TaskStatus(item.get("status", TaskStatus.PENDING.value)),
                    duration_ms=int(item.get("duration_ms", 0)), summary=item.get("summary", ""),
                    tools_used=item.get("tools_used", []), artifacts=item.get("artifacts", []),
                )
                for item in data.get("tasks", [])
            ],
        )

    @classmethod
    def _serialize_outputs(cls, context: AgentContext) -> dict:
        return {
            key: {
                task_id: {
                    "value": cls._encode_value(entry.value),
                    "producer": entry.producer,
                    "version": entry.version,
                    "timestamp": entry.timestamp,
                    "metadata": cls._encode_value(entry.metadata),
                }
                for task_id, entry in entries.items()
            }
            for key, entries in context.outputs.items()
        }

    @classmethod
    def _restore_outputs(cls, raw: dict) -> dict[str, dict[str, AgentOutput]]:
        return {
            key: {
                task_id: AgentOutput(
                    value=cls._decode_value(entry["value"]), producer=entry.get("producer", ""),
                    version=int(entry.get("version", 1)), timestamp=float(entry.get("timestamp", 0)),
                    metadata=cls._decode_value(entry.get("metadata", {})),
                )
                for task_id, entry in entries.items()
            }
            for key, entries in raw.items()
        }

    async def create_run(self, context: AgentContext, *, status: str = "planning") -> None:
        now = time.time()
        payload = {
            "run_id": context.run_id,
            "user_id": str(context.user_id) if context.user_id is not None else "",
            "session_id": context.session_id or "",
            "question": context.question,
            "document_ids": self._json(context.document_ids or []),
            "history": self._json([self._history_item(item) for item in context.history or []]),
            "preferences": self._json(context.preferences or {}),
            "status": status,
            "created_at": str(now),
            "updated_at": str(now),
        }
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self.meta_key(context.run_id), mapping=payload)
        pipe.expire(self.meta_key(context.run_id), settings.run_state_ttl_seconds)
        await pipe.execute()

    async def checkpoint_plan(self, context: AgentContext) -> None:
        if not context.plan:
            raise ValueError("cannot checkpoint an empty task graph")
        now = time.time()
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self.meta_key(context.run_id), mapping={
            "resolved_question": context.resolved_question or context.question,
            "planner_duration_ms": str(context.planner_duration_ms or 0),
            "task_graph": self._json(self._serialize_plan(context.plan)),
            "outputs": self._json(self._serialize_outputs(context)),
            "status": "running",
            "updated_at": str(now),
        })
        pipe.hset(self.tasks_key(context.run_id), mapping={
            task.id: self._json({"status": task.status.value, "agent": task.agent, "updated_at": now})
            for task in context.plan.tasks
        })
        for key in (self.meta_key(context.run_id), self.tasks_key(context.run_id)):
            pipe.expire(key, settings.run_state_ttl_seconds)
        await pipe.execute()

    async def checkpoint_task(self, context: AgentContext, task: TaskNode, *, status: str | None = None,
                              error: str = "") -> None:
        if not context.plan:
            return
        now = time.time()
        task_status = status or task.status.value
        task_data = {
            "status": task_status, "agent": task.agent, "summary": task.summary,
            "duration_ms": task.duration_ms, "tools_used": task.tools_used,
            "artifacts": task.artifacts, "error": error, "updated_at": now,
        }
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self.tasks_key(context.run_id), task.id, self._json(task_data))
        pipe.hset(self.meta_key(context.run_id), mapping={
            "task_graph": self._json(self._serialize_plan(context.plan)),
            "outputs": self._json(self._serialize_outputs(context)),
            "updated_at": str(now),
        })
        for key in (self.meta_key(context.run_id), self.tasks_key(context.run_id)):
            pipe.expire(key, settings.run_state_ttl_seconds)
        await pipe.execute()

    async def set_status(self, run_id: str, status: str, *, error: str = "") -> None:
        mapping = {"status": status, "updated_at": str(time.time())}
        if error:
            mapping["error"] = error
        await self.client.hset(self.meta_key(run_id), mapping=mapping)
        await self.client.expire(self.meta_key(run_id), settings.run_state_ttl_seconds)

    async def renew_lease(self, run_id: str, worker_id: str) -> bool:
        lease_key = self.lease_key(run_id)
        current = await self.client.get(lease_key)
        if current and current != worker_id:
            return False
        await self.client.set(lease_key, worker_id, ex=settings.run_lease_seconds)
        return True

    async def claim_recovery(self, run_id: str, worker_id: str) -> bool:
        """Atomically claim a run only when its previous worker lease expired."""
        claimed = await self.client.set(
            self.lease_key(run_id), worker_id, ex=settings.run_lease_seconds, nx=True,
        )
        if claimed:
            await self.set_status(run_id, "recovering")
        return bool(claimed)

    async def append_event(self, run_id: str, event_type: str, data: dict) -> str:
        event_id = await self.client.xadd(
            self.events_key(run_id), {"type": event_type, "data": self._json(data)},
            maxlen=self.EVENT_MAXLEN, approximate=True,
        )
        await self.client.expire(self.events_key(run_id), settings.run_state_ttl_seconds)
        return event_id

    async def read_events(self, run_id: str, after_id: str = "0-0") -> list[tuple[str, str, dict]]:
        rows = await self.client.xrange(self.events_key(run_id), min=f"({after_id}" if after_id != "0-0" else "-", max="+")
        return [(event_id, fields["type"], json.loads(fields["data"])) for event_id, fields in rows]

    async def load_context(self, run_id: str, event_bus=None) -> AgentContext | None:
        raw = await self.client.hgetall(self.meta_key(run_id))
        if not raw:
            return None
        context = AgentContext(
            question=raw["question"], session_id=raw.get("session_id") or None,
            user_id=int(raw["user_id"]) if raw.get("user_id") else None,
            document_ids=json.loads(raw.get("document_ids", "[]")),
            history=json.loads(raw.get("history", "[]")), preferences=json.loads(raw.get("preferences", "{}")),
            run_id=run_id, event_bus=event_bus, resolved_question=raw.get("resolved_question") or None,
            planner_duration_ms=int(raw.get("planner_duration_ms", "0") or 0),
        )
        if raw.get("task_graph"):
            context.plan = self._deserialize_plan(json.loads(raw["task_graph"]))
        context.outputs = self._restore_outputs(json.loads(raw.get("outputs", "{}")))
        return context

    async def get_run_status(self, run_id: str) -> str | None:
        return await self.client.hget(self.meta_key(run_id), "status")

    async def get_run_meta(self, run_id: str) -> dict[str, str]:
        """Return persisted metadata for ownership checks and session recovery."""
        return await self.client.hgetall(self.meta_key(run_id))

    async def find_active_run(self, *, user_id: int, session_id: str) -> dict[str, str] | None:
        """Find the newest unfinished run for one user's conversation session."""
        active_statuses = {"planning", "running", "executing", "recovering", "finalizing"}
        candidate: dict[str, str] | None = None
        async for key in self.client.scan_iter(match=f"{self.PREFIX}:*:meta"):
            raw = await self.client.hgetall(key)
            if (
                raw.get("user_id") != str(user_id)
                or raw.get("session_id") != str(session_id)
                or raw.get("status") not in active_statuses
            ):
                continue
            if candidate is None or float(raw.get("updated_at", 0)) > float(candidate.get("updated_at", 0)):
                candidate = raw
        return candidate

    async def list_expired_leases(self) -> list[str]:
        """Return active runs whose worker lease disappeared after a process crash."""
        run_ids: list[str] = []
        async for key in self.client.scan_iter(match=f"{self.PREFIX}:*:meta"):
            raw = await self.client.hgetall(key)
            if raw.get("status") not in {"planning", "running", "executing", "finalizing"}:
                continue
            run_id = raw.get("run_id", "")
            if run_id and not await self.client.exists(self.lease_key(run_id)):
                run_ids.append(run_id)
        return run_ids
