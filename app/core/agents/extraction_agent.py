import asyncio
import json
import logging
import re

from app.core.agents.base_agent import BaseAgent
from app.core.agent_context import AgentContext
from app.core.llm_factory import create_llm
from app.core.prompt_manager import PromptManager
from app.models.capability import AgentCapability
from app.models.data_types import DocumentBundle, Evidence, KnowledgeObject

logger = logging.getLogger(__name__)


class ExtractionAgent(BaseAgent):
    """提取 Agent：纯 LLM，无工具，从 DocumentBundle 提取结构化 KnowledgeObject

    Map-Reduce 策略：按 source 分片，每个文档独立并行 LLM 提取后合并结果。
    """

    name = "Extractor"
    capability = AgentCapability(
        name="extractor",
        description="从文档中提取结构化知识对象",
        inputs={"knowledge_document": DocumentBundle},
        required_inputs={"knowledge_document"},
        outputs={
            "knowledge_objects": list[KnowledgeObject],
            "evidence": list[Evidence],
            "sources": list[dict],
        },
        tools=[],
        merge_policy={
            "knowledge_objects": "append",
            "evidence": "dedup",
            "sources": "dedup",
        },
    )

    FALLBACK_SYSTEM_PROMPT = (
        "你是一个信息提取专家。根据用户问题，从提供的文档内容中提取结构化信息。\n\n"
        "规则：\n"
        "- 对当前文档，先识别文档主题，再提取其结构化属性\n"
        "- 如果文档不包含某属性的信息，omit 该 key 而非填空值\n"
        "- 只输出 JSON，不要任何自然语言\n\n"
        "输出格式：\n"
        "{\n"
        '  "knowledge_objects": [\n'
        "    {\n"
        '      "topic": "文档主题",\n'
        '      "attributes": {"key1": "value1", "key2": ["item1", "item2"]},\n'
        '      "source": "文件名",\n'
        '      "confidence": 0.95\n'
        "    }\n"
        "  ],\n"
        '  "evidence": [\n'
        "    {\n"
        '      "statement": "事实陈述",\n'
        '      "source": "文件名",\n'
        '      "evidence_type": "text"\n'
        "    }\n"
        "  ]\n"
        "}"
    )

    async def run(self, context: AgentContext, knowledge_document: DocumentBundle = None, **kwargs) -> AgentContext:
        bundle = knowledge_document or DocumentBundle(chunks=[])
        question = context.question

        if not bundle.chunks:
            logger.warning("[Extractor] 无文档需要提取")
            context.set_output("knowledge_objects", [], producer="extractor")
            context.set_output("evidence", [], producer="extractor")
            context.set_output("sources", [], producer="extractor")
            return context

        # 按 source 分组
        doc_groups = self._group_by_source(bundle)

        # Map：每个文档独立并行 LLM 提取
        tasks = [
            self._extract_single_source(source, chunks, question)
            for source, chunks in doc_groups
        ]
        results = await asyncio.gather(*tasks)

        # Reduce：合并结果
        all_knowledge_objects = []
        all_evidence = []
        for kos, evs in results:
            all_knowledge_objects.extend(kos)
            all_evidence.extend(evs)

        sources = self._extract_sources(all_knowledge_objects, all_evidence)

        context.set_output("knowledge_objects", all_knowledge_objects, producer="extractor")
        context.set_output("evidence", all_evidence, producer="extractor")
        context.set_output("sources", sources, producer="extractor")

        logger.info("[Extractor] 提取 %d 个知识对象, %d 条证据（Map-Reduce: %d 个文档）",
                    len(all_knowledge_objects), len(all_evidence), len(doc_groups))

        return context

    # ==================== Map-Reduce 核心 ====================

    @staticmethod
    def _group_by_source(bundle: DocumentBundle) -> list[tuple[str, list]]:
        """按 source 分组，返回 [(source, [chunks])] 列表"""
        from collections import OrderedDict
        groups = OrderedDict()
        for c in bundle.chunks:
            groups.setdefault(c.source, []).append(c)
        return list(groups.items())

    async def _extract_single_source(self, source: str, chunks: list, question: str) -> tuple[list[KnowledgeObject], list[Evidence]]:
        """对单个文档执行 LLM 提取"""
        doc_text = self._format_single_doc(source, chunks)

        system_prompt = self.FALLBACK_SYSTEM_PROMPT
        try:
            system_prompt = PromptManager.get("extractor", "system") or self.FALLBACK_SYSTEM_PROMPT
        except Exception:
            pass

        user_prompt = f"用户问题：{question}\n\n文档内容（{source}）：\n{doc_text}"

        llm = create_llm(temperature=0, max_tokens=8192)
        try:
            result = await llm.ainvoke([
                ("system", system_prompt),
                ("human", user_prompt),
            ])

            raw_text = result.content
            kos, evs = self._parse_output(raw_text)

            logger.info("[Extractor] 文档 '%s' 提取 %d 个知识对象, %d 条证据",
                        source, len(kos), len(evs))
            return kos, evs

        except Exception as e:
            logger.error("[Extractor] 文档 '%s' 提取失败: %s", source, e)
            return [], []

    @staticmethod
    def _format_single_doc(source: str, chunks: list) -> str:
        """格式化单个文档的 chunks 为文本"""
        parts = []
        for c in chunks:
            parts.append(c.content)
        return "\n".join(parts)

    # ==================== 解析 ====================

    def _parse_output(self, text: str) -> tuple[list[KnowledgeObject], list[Evidence]]:
        """解析 LLM JSON 输出为 KnowledgeObject + Evidence"""
        from app.core.utils import extract_json
        data = extract_json(text)
        if data is None or not isinstance(data, dict):
            logger.warning("[Extractor] JSON 解析失败，尝试正则兜底")
            return [], self._extract_evidence_fallback(text)

        knowledge_objects = []
        for item in data.get("knowledge_objects", []):
            if isinstance(item, dict) and "topic" in item:
                knowledge_objects.append(KnowledgeObject(
                    topic=item["topic"],
                    attributes=item.get("attributes", {}),
                    source=item.get("source", ""),
                    confidence=item.get("confidence", 1.0),
                ))

        evidence_list = []
        for item in data.get("evidence", []):
            if isinstance(item, dict) and "statement" in item:
                evidence_list.append(Evidence(
                    statement=item["statement"],
                    source=item.get("source", ""),
                    evidence_type=item.get("evidence_type", "text"),
                    metadata=item.get("metadata", {}),
                ))

        return knowledge_objects, evidence_list

    def _extract_evidence_fallback(self, text: str) -> list[Evidence]:
        results = []
        stmt_pattern = re.compile(
            r'"statement"\s*:\s*"((?:(?!",\s*"(?:source|evidence_type|metadata)).)+)"'
        )
        statements = stmt_pattern.findall(text)
        src_pattern = re.compile(
            r'"source"\s*:\s*"((?:(?!",\s*"(?:source|evidence_type|metadata)).)+)"'
        )
        sources = src_pattern.findall(text)
        for i, statement in enumerate(statements):
            source = sources[i] if i < len(sources) else ""
            results.append(Evidence(
                statement=statement.strip(),
                source=source.strip(),
                evidence_type="text",
            ))
        if results:
            logger.warning("[Extractor] 正则兜底提取到 %d 条 Evidence", len(results))
        return results

    @staticmethod
    def _extract_sources(knowledge_objects: list[KnowledgeObject], evidence: list[Evidence]) -> list[dict]:
        """从 knowledge_objects 和 evidence 中提取来源信息"""
        seen = {}
        for ko in knowledge_objects:
            if ko.source and ko.source not in seen:
                attrs_str = "; ".join(f"{k}={v}" if not isinstance(v, list) else f"{k}={', '.join(str(x) for x in v)}"
                                      for k, v in list(ko.attributes.items())[:3])
                seen[ko.source] = {"file_name": ko.source, "content": attrs_str[:200]}
        for ev in evidence:
            if ev.source and ev.source not in seen:
                seen[ev.source] = {"file_name": ev.source, "content": ev.statement[:200]}
        return list(seen.values())
