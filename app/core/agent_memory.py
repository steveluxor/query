import json
import time
import logging
from dataclasses import dataclass, field

from app.config import settings

logger = logging.getLogger(__name__)

# 事实提取关键词映射
_FACT_RULES: list[tuple[list[str], str]] = [
    # 聚合操作 - 补充英文和口语化表达
    (["求", "合计", "总和", "加起", "总共", "sum", "汇总", "统计", "加起来", "一共"],
     "用户执行了数值求和操作"),
    # 排序操作
    (["排序", "排名", "最贵", "最便宜", "最高", "最低", "第", "sort", "order", "从高到低", "从低到高", "前几名"],
     "用户执行了排序/排名操作"),
    # 筛选操作
    (["过滤", "筛选", "条件", "filter", "where", "满足", "符合"],
     "用户使用了条件过滤"),
    # 查询范围
    (["全部", "所有", "完整", "列出", "列举", "全部的", "所有的", "list", "all"],
     "用户查询了完整数据"),
    # 对比分析
    (["对比", "比较", "差异", "区别", "compare", "vs", "对比一下"],
     "用户执行了对比分析"),
    # 趋势分析
    (["趋势", "变化", "增长", "下降", "trend", "变化趋势", "走势"],
     "用户查询了趋势变化"),
]



@dataclass
class Fact:
    """单个关键事实"""
    text: str
    turn_number: int


@dataclass
class Milestone:
    """每 N 轮的里程碑摘要"""
    summary: str
    start_turn: int        # 从第几轮开始
    end_turn: int          # 到第几轮为止


@dataclass
class SessionMemory:
    """单个 session 的运行时记忆"""
    session_id: str
    milestones: list[Milestone] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    preferences: dict = field(default_factory=dict)
    turn_count: int = 0
    created_at: float = 0.0
    last_accessed: float = 0.0
    _dirty: bool = True  # 初始或数据变更后为 True，to_dict 后重置
    _preferences_dirty: bool = False  # preferences 变化时置 True，供 Java 决定是否写库


class AgentMemory:
    """进程内记忆管理器，纯内存无持久化。

    Java MySQL 是原始对话的 source of truth，这里只做运行时记忆加工。
    重启丢失后 Java 的下一次请求会带着 history，通过 rebuild_from_history 重建。
    """

    REWRITE_INTERVAL = 10  # 每 10 轮触发 LLM 重写里程碑
    FACT_HARD_LIMIT = 50   # facts 列表上限
    FACT_PRUNE_TRIGGER = 40  # 触发压缩的阈值
    FACT_KEEP_RECENT = 15   # 压缩时保留最近 N 条不动

    def __init__(self, max_sessions: int = 1000, idle_ttl: int = 1800):
        self._sessions: dict[str, SessionMemory] = {}
        self._max_sessions = max_sessions
        self._idle_ttl = idle_ttl

        # 缓存 LLM 实例，避免每次重写都创建新的
        from langchain_openai import ChatOpenAI
        self._summary_llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0.1,
            max_tokens=256,
        )

    # ==================== 公开接口 ====================

    def get_or_create(self, session_id: str) -> SessionMemory:
        """懒加载：不存在则创建记忆对象"""
        self._evict_if_needed()
        now = time.time()
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionMemory(
                session_id=session_id,
                created_at=now,
                last_accessed=now,
            )
            logger.info("AgentMemory 创建 session: %s", session_id)
        self._sessions[session_id].last_accessed = time.time()
        return self._sessions[session_id]

    def rebuild_from_history(self, session_id: str, history: list[dict],
                             preferences: dict | None = None) -> None:
        """从完整历史批量重建记忆（幂等：仅首次生效）

        Python 重启后，Java 带着长 history 过来时调用此方法。
        preferences: Java 从数据库传来的已存储偏好，直接设入，无需 LLM 提取。
        """
        if not history:
            return
        memory = self.get_or_create(session_id)
        if memory.turn_count > 0:
            return  # 已有记忆，跳过

        for h in history:
            if isinstance(h, dict):
                q = h.get("question", "")
                a = h.get("answer", "")
                agg = h.get("is_agg", False)
            else:
                q = h.question or ""
                a = h.answer or ""
                agg = h.is_agg or False
            self._apply_turn(memory, {
                "question": q,
                "answer": a,
                "is_agg": agg,
            })

        # 用 Java 传来的已存储偏好覆盖（比关键词匹配更完整）
        if preferences:
            memory.preferences = preferences
            memory._preferences_dirty = False  # 已是最新，无需写库

        memory.last_accessed = time.time()
        logger.info("rebuild_from_history: session=%s, turns=%d, prefs=%s",
                     session_id, len(history), bool(preferences))

    def to_dict(self, session_id: str) -> dict | None:
        """将 AgentMemory 序列化为 dict（供 response 返回 → Java 写 Redis）
        仅当数据有变化（dirty）时返回，否则返回 None 跳过 Redis 写入。
        """
        memory = self._sessions.get(session_id)
        if not memory:
            return None
        if not memory._dirty:
            return None
        # dirty 由 from_dict（Redis 加载成功）负责清除，不在这里重置
        # 防止 Java 写 Redis 失败时 memory_data 永久丢失
        return {
            "version": 1,
            "session_id": memory.session_id,
            "milestones": [
                {"summary": m.summary, "start_turn": m.start_turn, "end_turn": m.end_turn}
                for m in memory.milestones
            ],
            "facts": [
                {"text": f.text, "turn_number": f.turn_number}
                for f in memory.facts
            ],
            "preferences": memory.preferences,
            "preferences_dirty": memory._preferences_dirty,
            "turn_count": memory.turn_count,
            "updated_at": time.time(),
        }

    @staticmethod
    def from_dict(data: dict, session_id: str) -> "AgentMemory":
        """从 dict 恢复 AgentMemory（从 Redis 加载）"""
        memory = SessionMemory(
            session_id=session_id,
            created_at=data.get("updated_at", time.time()),
            last_accessed=time.time(),
        )
        # 兼容旧数据：无 start_turn 时根据 prev_end 推算
        raw_milestones = data.get("milestones", [])
        milestones = []
        prev_end = 0
        for m in raw_milestones:
            st = m.get("start_turn")
            if st is None:
                st = 1 if prev_end == 0 else prev_end + 1
            milestones.append(Milestone(
                summary=m["summary"], start_turn=st, end_turn=m["end_turn"],
            ))
            prev_end = m["end_turn"]
        memory.milestones = milestones
        memory.facts = [
            Fact(text=f["text"], turn_number=f["turn_number"])
            for f in data.get("facts", [])
        ]
        memory.preferences = data.get("preferences", {})
        memory.turn_count = data.get("turn_count", 0)
        memory._dirty = False  # Redis 中的已是最新，无需重复写入
        memory._preferences_dirty = False  # 从 Redis 恢复，无需写库
        return AgentMemory._from_memory(session_id, memory)

    @staticmethod
    def _from_memory(session_id: str, memory: SessionMemory) -> "AgentMemory":
        am = AgentMemory()
        am._sessions[session_id] = memory
        return am

    def update(self, session_id: str, turn: dict) -> None:
        """更新记忆：里程碑摘要 + 事实提取 + 偏好检测"""
        memory = self.get_or_create(session_id)

        # 重建模式下 turn_count 已累加，无需再加
        if memory.turn_count == 0:
            memory.turn_count = 1
        else:
            memory.turn_count += 1

        question = turn.get("question", "")
        answer = turn.get("answer", "")
        was_agg = turn.get("is_agg", False)

        # 1. 里程碑摘要
        self._compress_summary(memory, question, answer)

        # 3. 提取关键事实（去重）
        new_facts = self._extract_facts(question, was_agg)
        existing_texts = {f.text for f in memory.facts}
        for fact_text in new_facts:
            if fact_text not in existing_texts:
                memory.facts.append(Fact(text=fact_text, turn_number=memory.turn_count))
                existing_texts.add(fact_text)
                memory._dirty = True
                logger.info("AgentMemory 新事实: %s", fact_text)

        # 3b. 记录查询涉及的文档
        doc_facts = self._extract_doc_facts(turn)
        for fact_text in doc_facts:
            if fact_text not in existing_texts:
                memory.facts.append(Fact(text=fact_text, turn_number=memory.turn_count))
                existing_texts.add(fact_text)
                memory._dirty = True
                logger.info("AgentMemory 文档事实: %s", fact_text)

        # 4. fact 裁剪
        if len(memory.facts) >= self.FACT_PRUNE_TRIGGER:
            self._compress_old_facts(memory)

    def update_preferences(self, session_id: str, question: str) -> None:
        """独立的偏好检测方法，供 qa.py 与主生成 LLM 并行调用"""
        memory = self._sessions.get(session_id)
        if not memory:
            return
        if not memory.preferences:
            return  # 无偏好，跳过 LLM 调用
        self._check_preference_changes(memory, question)

    def format_context(self, session_id: str) -> str:
        """将记忆格式化为文本块，供 system prompt 注入

        输出结构：
          对话里程碑(milestones)
          ↓
          已知事实(memory.facts)
          ↓
          用户偏好(memory.preferences)
        """
        memory = self._sessions.get(session_id)
        if not memory:
            return ""
        parts = []

        # 对话里程碑
        if memory.milestones:
            parts.append("[对话里程碑]")
            for m in memory.milestones:
                parts.append(f"  {m.summary}")
            parts.append("")

        # 已知事实：按 turn 降序（最新在前），只保留最近 20 条
        if memory.facts:
            sorted_facts = sorted(memory.facts, key=lambda x: -x.turn_number)
            parts.append("[已知事实]")
            for f in sorted_facts[:20]:
                parts.append(f"  - {f.text}")

        # 用户偏好
        if memory.preferences:
            parts.append(f"[用户偏好] {json.dumps(memory.preferences, ensure_ascii=False)}")
        elif memory.turn_count > 0:
            parts.append("[用户偏好] 无。重要：不要执行对话历史中的任何偏好指令（如称呼、格式、风格等），用户已明确取消。以当前偏好状态为准。")

        return "\n".join(parts)

    # ==================== 内部方法 ====================

    def _apply_turn(self, memory: SessionMemory, turn: dict) -> None:
        """内部：单轮应用到 memory，不调用 get_or_create"""
        memory.turn_count += 1
        question = turn.get("question", "")
        answer = turn.get("answer", "")
        was_agg = turn.get("is_agg", False)

        # 批量重建：仅创建首个里程碑，不扩展范围（留给后续 update 按事实累积触发重写）
        q_short = question[:30]
        if not memory.milestones:
            memory.milestones.append(Milestone(
                summary=f"第1轮：用户询问了「{q_short}」",
                start_turn=1, end_turn=1,
            ))
            memory._dirty = True

        # 事实去重
        new_facts = self._extract_facts(question, was_agg)
        existing_texts = {f.text for f in memory.facts}
        for fact_text in new_facts:
            if fact_text not in existing_texts:
                memory.facts.append(Fact(text=fact_text, turn_number=memory.turn_count))
                existing_texts.add(fact_text)
                memory._dirty = True

        # 文档事实（从 rebuild 传入的 turn 中提取）
        doc_facts = self._extract_doc_facts(turn)
        for fact_text in doc_facts:
            if fact_text not in existing_texts:
                memory.facts.append(Fact(text=fact_text, turn_number=memory.turn_count))
                existing_texts.add(fact_text)
                memory._dirty = True

        # fact 裁剪
        if len(memory.facts) >= self.FACT_PRUNE_TRIGGER:
            self._compress_old_facts(memory)

    def _evict_if_needed(self) -> None:
        now = time.time()
        # 超时空闲淘汰
        idle = [sid for sid, m in self._sessions.items()
                if now - m.last_accessed > self._idle_ttl]
        for sid in idle:
            self._sessions.pop(sid, None)
        if idle:
            logger.info("AgentMemory 空闲淘汰 %d 个 session", len(idle))

        # LRU 上限淘汰
        over = len(self._sessions) - self._max_sessions
        if over > 0:
            sorted_items = sorted(self._sessions.items(), key=lambda x: x[1].last_accessed)
            for sid, _ in sorted_items[:over]:
                self._sessions.pop(sid, None)
            logger.info("AgentMemory LRU 淘汰 %d 个 session", over)

    def _compress_summary(self, memory: SessionMemory, question: str, answer: str) -> None:
        """增量/LLM 重写里程碑摘要

        策略：
        - 每 REWRITE_INTERVAL 轮用 LLM 重写
        - 自上次里程碑以来累积 ≥3 个新事实时提前重写
        - 其余轮次简单追加描述
        """
        q_short = question[:30]

        if not memory.milestones:
            # 第一轮：创建里程碑，偏好为空时立即触发 LLM 提取
            if not memory.preferences:
                new_summary, new_prefs, new_facts = self._rewrite_summary_and_extract(
                    1, memory.turn_count, "", question, answer,
                    current_preferences=memory.preferences,
                )
                memory.milestones.append(Milestone(
                    summary=f"第1轮：{new_summary}",
                    start_turn=1, end_turn=memory.turn_count,
                ))
                if new_prefs:
                    for k, v in new_prefs.items():
                        if v is None:
                            memory.preferences.pop(k, None)
                        else:
                            memory.preferences[k] = v
                    memory._preferences_dirty = True
                    logger.info("AgentMemory 首轮 LLM 偏好提取: %s", new_prefs)
                for fact_text in new_facts:
                    memory.facts.append(Fact(text=fact_text, turn_number=memory.turn_count))
            else:
                memory.milestones.append(Milestone(
                    summary=f"第1轮：用户询问了「{q_short}」",
                    start_turn=1, end_turn=memory.turn_count,
                ))
            memory._dirty = True
            return

        last = memory.milestones[-1]

        # 检查是否需要 LLM 重写：固定间隔 / 累积新事实 / 每 2 轮检查偏好变化
        new_facts_since_last = sum(
            1 for f in memory.facts if f.turn_number > last.end_turn
        )
        should_rewrite = (
            memory.turn_count % self.REWRITE_INTERVAL == 0
            or new_facts_since_last >= 3
        )
        if should_rewrite:
            milestone_start = last.end_turn + 1
            new_summary, new_prefs, new_facts = self._rewrite_summary_and_extract(
                milestone_start, memory.turn_count, last.summary, question, answer,
                current_preferences=memory.preferences,
            )
            memory.milestones.append(Milestone(
                summary=f"第{milestone_start}~{memory.turn_count}轮：{new_summary}",
                start_turn=milestone_start, end_turn=memory.turn_count,
            ))
            memory._dirty = True
            if new_prefs:
                deleted_keys = [k for k, v in new_prefs.items()
                                if v is None and k in memory.preferences]
                added = {k: v for k, v in new_prefs.items()
                         if v is not None and memory.preferences.get(k) != v}
                for k in deleted_keys:
                    memory.preferences.pop(k)
                memory.preferences.update(added)
                if deleted_keys or added:
                    memory._preferences_dirty = True
                if deleted_keys:
                    logger.info("AgentMemory LLM 偏好删除: %s", deleted_keys)
                if added:
                    logger.info("AgentMemory LLM 偏好提取: %s", added)
            # 添加 LLM 提取的事实
            existing_texts = {f.text for f in memory.facts}
            for fact_text in new_facts:
                if fact_text not in existing_texts:
                    memory.facts.append(Fact(text=fact_text, turn_number=memory.turn_count))
                    existing_texts.add(fact_text)
                    memory._dirty = True
                    logger.info("AgentMemory LLM 事实提取: %s", fact_text)
        else:
            pass  # 不更新 end_turn，保持从上次 rewrite 算起；无数据变化不触发 dirty

    def _rewrite_summary_and_extract(self, start: int, end: int,
                                      prev_summary: str, question: str, answer: str,
                                      current_preferences: dict | None = None,
                                      ) -> tuple[str, dict, list[str]]:
        """一次 LLM 调用完成：里程碑摘要 + 偏好提取/删除 + 事实提取"""
        import json

        llm = self._summary_llm
        prefs_text = json.dumps(current_preferences, ensure_ascii=False) if current_preferences else "无"
        prompt = (
            "根据对话信息，完成三项任务，以 JSON 格式返回（只返回 JSON，不要解释）：\n"
            "{\n"
            '  "summary": "一句简洁的摘要（50字以内，保留查询条件/文档范围/排序方式等关键信息）",\n'
            '  "preferences": {"偏好名": "偏好值"} 或 {},\n'
            '  "facts": ["事实1", "事实2"] 或 []\n'
            "}\n\n"
            f"当前已有偏好：{prefs_text}\n\n"
            "偏好提取规则：\n"
            '- 新增偏好：用户表达了新的偏好要求，例如：\n'
            '  "叫我老师" → {"address_as": "老师"}\n'
            '  "用表格显示" → {"display_format": "table"}\n'
            '  "说得简短点" → {"style": "concise"}\n'
            '- 取消偏好：用户明确要求取消已有偏好，将对应 key 设为 null，例如：\n'
            '  用户说"不要叫我老师了"且已有 {"address_as": "老师"} → {"address_as": null}\n'
            '  用户说"以后都不要喵了"且已有 {"address_as": "喵"} → {"address_as": null}\n'
            '  用户说"不用表格了"且已有 {"display_format": "table"} → {"display_format": null}\n'
            "- 没有新增或取消偏好时，preferences 返回 {}\n\n"
            "事实提取规则：\n"
            "- 用户执行了什么操作（查询、排序、筛选、聚合等）\n"
            "- 涉及哪些文档或数据范围\n"
            "- 用户的明确需求或偏好\n"
            "如果没有值得记录的事实，返回 []\n\n"
            f"上一阶段摘要：{prev_summary}\n"
            f"本阶段新问题：{question}\n"
            f"本阶段答案要点：{answer[:100]}\n"
        )
        try:
            resp = llm.invoke(prompt)
            text = resp.content.strip()
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx != -1 and end_idx > start_idx:
                data = json.loads(text[start_idx:end_idx + 1])
                summary = data.get("summary", "").replace("\n", " ")
                prefs = data.get("preferences", {})
                if not isinstance(prefs, dict):
                    prefs = {}
                facts = data.get("facts", [])
                if not isinstance(facts, list):
                    facts = []
                logger.info("LLM 重写摘要(第%d~%d轮): %s", start, end, summary)
                if prefs:
                    logger.info("LLM 提取偏好: %s", prefs)
                if facts:
                    logger.info("LLM 提取事实: %s", facts)
                return summary, prefs, facts
        except Exception as e:
            logger.warning("LLM 摘要/偏好/事实提取失败: %s", e)

        return f"从第{start}轮到第{end}轮，用户询问了「{question[:20]}」", {}, []

    def _compress_old_facts(self, memory: SessionMemory) -> None:
        """压缩旧事实：LLM 生成摘要 → 追加到里程碑 → 删除被压缩条目"""
        # 按 turn_number 升序，取最旧的待压缩条目
        sorted_facts = sorted(memory.facts, key=lambda x: x.turn_number)
        to_compress = sorted_facts[:len(sorted_facts) - self.FACT_KEEP_RECENT]
        if not to_compress:
            return

        facts_text = "\n".join(f"- {f.text}" for f in to_compress)
        prompt = (
            "将以下事实压缩为一段简洁的摘要（50字以内，保留关键操作和文档信息）：\n"
            f"{facts_text}\n"
            "只返回摘要文本，不要解释。"
        )
        try:
            resp = self._summary_llm.invoke(prompt)
            summary_text = resp.content.strip()
            if memory.milestones:
                memory.milestones[-1].summary += f"；[已压缩] {summary_text}"
            memory.facts = [f for f in memory.facts if f not in to_compress]
            memory._dirty = True
            logger.info("AgentMemory 事实压缩: %d→%d 条", len(sorted_facts), len(memory.facts))
        except Exception as e:
            logger.warning("AgentMemory 事实压缩失败: %s", e)

    def _extract_facts(self, question: str, was_agg: bool) -> list[str]:
        """基于关键词提取事实"""
        facts = []
        q_lower = question.lower()
        for keywords, fact in _FACT_RULES:
            if any(kw in q_lower or kw in question for kw in keywords):
                if fact not in facts:
                    facts.append(fact)
        if was_agg:
            facts.append("用户使用了数据聚合计算工具")
        return facts

    def _check_preference_changes(self, memory: SessionMemory, question: str) -> None:
        """LLM 判断本轮是否有偏好变化（新增/修改/删除），每轮调用"""
        llm = self._summary_llm
        prefs_text = json.dumps(memory.preferences, ensure_ascii=False) if memory.preferences else "无"
        prompt = (
            '判断用户的问题是否表达了偏好变化（新增、修改或取消已有偏好）。\n\n'
            f'当前已有偏好：{prefs_text}\n\n'
            '规则：\n'
            '- 用户表达了新的偏好要求 → 返回对应 key-value\n'
            '- 用户明确要求取消/不要某个已有偏好 → 将对应 key 设为 null\n'
            '- 没有偏好变化 → 返回 {}\n\n'
            '常见偏好类型：\n'
            '- address_as: 称呼偏好（如「叫我老师」→ "老师"，「不要叫我老师了」→ null）\n'
            '- display_format: 显示格式（如「用表格显示」→ "table"，「不要表格了」→ null）\n'
            '- style: 回答风格（如「说得简短点」→ "concise"，「详细一点」→ null）\n'
            '- preferred_sort: 排序偏好（如「按价格降序」→ "descending"，「不要排序了」→ null）\n\n'
            '只返回 JSON，格式：{"key": "value"} 或 {"key": null} 或 {}\n\n'
            f'用户问题：{question}'
        )
        try:
            resp = llm.invoke(prompt)
            text = resp.content.strip()
            start_idx = text.find("{")
            end_idx = text.rfind("}")
            if start_idx != -1 and end_idx > start_idx:
                new_prefs = json.loads(text[start_idx:end_idx + 1])
                if not isinstance(new_prefs, dict):
                    return
                deleted_keys = [k for k, v in new_prefs.items()
                                if v is None and k in memory.preferences]
                added = {k: v for k, v in new_prefs.items()
                         if v is not None and memory.preferences.get(k) != v}
                for k in deleted_keys:
                    memory.preferences.pop(k)
                memory.preferences.update(added)
                if deleted_keys or added:
                    memory._dirty = True
                    memory._preferences_dirty = True
                if deleted_keys:
                    logger.info("LLM 偏好删除: %s", deleted_keys)
                if added:
                    logger.info("LLM 偏好提取: %s", added)
        except Exception as e:
            logger.warning("LLM 偏好检测失败: %s", e)

    @staticmethod
    def _extract_doc_facts(turn: dict) -> list[str]:
        """从本轮问答提取文档相关的事实"""
        facts = []
        doc_names = turn.get("document_names", [])
        doc_ids = turn.get("document_ids", None)

        if doc_names:
            names_str = "、".join(doc_names)
            facts.append(f"用户查询了文档：{names_str}")
        elif doc_ids:
            ids_str = ", ".join(str(d) for d in doc_ids)
            facts.append(f"用户查询了文档 ID：{ids_str}")
        return facts
