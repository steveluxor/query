import logging

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.core.vector_store import VectorStore

logger = logging.getLogger(__name__)


PROMPT_TEMPLATE = """你是一个智能问答助手。以下是从不同文档中检索到的相关内容，每段都标注了来源文件名。

{context}

{history}

用户问题：{question}

要求：
1. 直接回答问题，不要提及文档检索过程或"没有找到相关文档"
2. 只引用与问题直接相关的文档内容，忽略无关文档
3. 引用时请注明来源文件名
4. 如果文档中有相关信息，优先使用文档内容回答
5. 如果文档中没有相关信息，直接用你的知识回答，不要提及检索过程
6. 严格遵守用户在问题中提出的所有要求和限制条件"""

REWRITE_SYSTEM_PROMPT = """你是一个查询改写助手。将用户的问题改写为完整的搜索查询，以便在文档库中检索相关内容。

要求：
- 将缩写、简写补全为完整名称（如"qt"→"Qt"、"py"→"Python"）
- 如果问题是关于个人（如"汪小丹"），补充可能的学号、实验名称等关键词
- 如果问题很短（少于5个字），扩展为更具体的查询
- 保持关键术语的大小写规范

只返回改写后的查询，不要任何额外内容。"""

REWRITE_WITH_HISTORY_PROMPT = """你是一个查询改写助手。根据对话历史，将用户的最新问题改写为独立、自包含的查询，使得不需要看历史也能理解查询意图。

只返回改写后的查询，不要任何额外内容。如果问题本身已经很清晰，直接返回原问题。"""

NEEDS_RAG_PROMPT = """判断以下问题是否需要检索文档来回答。只返回"需要"或"不需要"，不要任何其他内容。

不需要检索文档的情况：问候、自我介绍、闲聊、通用知识（如"你是谁"、"你好"、"今天天气"等）
需要检索文档的情况：涉及具体文档内容、专业知识、特定信息查询

问题：{question}

回答："""

DIRECT_ANSWER_PROMPT = """你是一个智能问答助手。请直接回答用户的问题。

{history}

用户问题：{question}"""


class RAGEngine:
    """RAG 引擎：向量检索 → 构建 Prompt → LLM 生成"""

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0.1,
            max_tokens=4096,
        )
        self.prompt = ChatPromptTemplate.from_template(PROMPT_TEMPLATE)

    # ChromaDB 余弦距离阈值：高于此值视为不相关，排除
    SCORE_THRESHOLD = 0.85
    DEFAULT_TOP_K = 10  # 默认返回 chunk 数量

    MAX_HISTORY_TURNS = 5

    def _determine_top_k(self, filtered: list) -> int:
        """根据分数分布动态决定 top_k"""
        if len(filtered) <= 3:
            return len(filtered)
        scores = [s for _, s in filtered]
        max_gap = 0
        gap_index = len(scores)
        for i in range(len(scores) - 1):
            gap = scores[i + 1] - scores[i]
            if gap > max_gap:
                max_gap = gap
                gap_index = i + 1
        return max(3, min(gap_index, 15))

    def _select_strategy(self, question: str) -> str:
        """判断采用何种选块策略：'relevance' 或 'diversity'"""
        diversity_keywords = ["对比", "区别", "比较", "优缺点", "哪些", "所有", "全部", "总结", "分析", "不同", "相同",
                              "vs", "versus", "difference", "compare", "pros and cons", "all"]
        if len(question) > 30 or any(kw in question for kw in diversity_keywords):
            return "diversity"
        return "relevance"

    # 聚合类关键词：需要全量检索而非 Top-K
    AGGREGATION_KEYWORDS = [
        "总共", "一共", "合计", "总计", "总和", "整个", "所有", "全部",
        "sum", "total", "多少条", "多少个", "有几个", "花了多少", "多少钱",
    ]

    def _is_aggregation_query(self, question: str) -> bool:
        return any(kw in question for kw in self.AGGREGATION_KEYWORDS)

    def _select_by_diversity(self, filtered: list, top_k: int) -> list:
        """轮询选取，保证文档多样性"""
        groups = {}
        for doc, score in filtered:
            did = doc.metadata.get("document_id")
            groups.setdefault(did, []).append((doc, score))
        selected = []
        doc_ids = list(groups.keys())
        while len(selected) < top_k and doc_ids:
            for did in list(doc_ids):
                if groups[did]:
                    selected.append(groups[did].pop(0))
                    if len(selected) >= top_k:
                        break
                if not groups[did]:
                    doc_ids.remove(did)
        if len(selected) < top_k:
            remaining = [(doc, score) for doc, score in filtered if (doc, score) not in selected]
            remaining.sort(key=lambda x: x[1])
            selected.extend(remaining[:top_k - len(selected)])
        return selected

    def _format_history(self, history: list | None) -> str:
        """将历史问答格式化为可读文本（兼容 dict 和 HistoryItem）"""
        if not history:
            return ""
        history = history[-self.MAX_HISTORY_TURNS:]
        lines = ["<历史对话>"]
        for h in history:
            if isinstance(h, dict):
                q = h.get("question", "")
                a = h.get("answer", "")
            else:
                q = getattr(h, "question", "")
                a = getattr(h, "answer", "")
            lines.append(f"  用户：{q}")
            lines.append(f"  助手：{a}")
            lines.append("")
        lines.append("</历史对话>")
        return "\n".join(lines)

    def _needs_rag(self, question: str) -> bool:
        """判断问题是否需要检索文档"""
        prompt = NEEDS_RAG_PROMPT.format(question=question)
        try:
            response = self.llm.invoke(prompt)
            answer = response.content.strip()
            return "不需要" not in answer
        except Exception:
            return True  # 出错时默认走 RAG


    def _rewrite_query(self, question: str, history: list[dict] | None) -> str:
        """将问题改写为更利于检索的查询——无论有无历史都会改写"""
        if history:
            history_text = self._format_history(history)
            prompt = f"{REWRITE_WITH_HISTORY_PROMPT}\n\n对话历史：\n{history_text}\n\n最新问题：{question}\n\n改写后的查询："
        else:
            prompt = f"{REWRITE_SYSTEM_PROMPT}\n\n用户问题：{question}\n\n将以上问题扩展为完整的搜索查询，补充可能的文档标题关键词、学号、实验名称等。\n\n改写后的查询："
        try:
            response = self.llm.invoke(prompt)
            rewritten = response.content.strip()
            if rewritten:
                return rewritten
        except Exception:
            pass
        return question

    def _extract_column_from_question(self, question: str, chunks: list) -> str | None:
        """从用户问题中提取明确指定的列名（如"使用结果列"→"结果"），只匹配数值列"""
        if not chunks or not question:
            return None
        # 收集所有 chunk 中的 key 名，并统计每个 key 的数值比例
        key_values: dict[str, list[float]] = {}
        for doc, _ in chunks[:300]:
            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, val = line.partition(":")
                    k = k.strip()
                    val = val.strip().replace(",", "")
                    if k:
                        try:
                            key_values.setdefault(k, []).append(float(val))
                        except ValueError:
                            key_values.setdefault(k, [])
        numeric_keys = {k for k, v in key_values.items() if v}
        logger.info("chunk中的所有key: %s", sorted(key_values.keys()))
        logger.info("数值列key: %s", sorted(numeric_keys))
        if not numeric_keys:
            return None
        # 在问题中查找出现的数值列 key 名
        matched = []
        for key in numeric_keys:
            if key in question:
                matched.append(key)
        if matched:
            best = max(matched, key=len)
            logger.info("从问题中提取到列名: '%s' (匹配key=%s)", best, matched)
            return best
        return None

    def _identify_target_key(self, question: str, chunks: list) -> str | None:
        """让 LLM 从原始 chunk 内容中判断要加总的 key 名"""
        if not chunks:
            return None
        # 发前 5 个 chunk 的原始内容
        sample = "\n---\n".join(doc.page_content for doc, _ in chunks[:5])
        prompt = (
            f"以下是文档中的几条记录（key:value 格式）：\n{sample}\n\n"
            f"问题：{question}\n"
            f"请判断要加总求和的是哪个 key 对应的数值？\n"
            f"注意：只能选择值为数字的 key（如'结果'、'金额'、'价格'等），不能选择文本类型的 key。\n"
            f"只返回 key 名，不要其他内容。"
        )
        try:
            response = self.llm.invoke(prompt)
            key = response.content.strip().strip("'\"")
            logger.info("列识别: LLM返回key='%s'", key)
            return key
        except Exception:
            pass
        return None

    def _sum_by_key(self, chunks: list, key_name: str) -> tuple[float, int, list[float], str]:
        """从所有 chunk 中提取指定 key 的数值并求和，找不到数值时自动回退"""
        values = []
        for doc, _ in chunks:
            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, val = line.partition(":")
                    if k.strip() == key_name:
                        val = val.strip().replace(",", "")
                        try:
                            values.append(float(val))
                        except ValueError:
                            pass

        # 回退：如果识别的 key 无法提取数值，尝试所有 key 找到第一个能求和的
        if not values:
            logger.warning("列'%s'无法提取数值，尝试自动识别数值列", key_name)
            key_candidates: dict[str, list[float]] = {}
            for doc, _ in chunks[:300]:
                for line in doc.page_content.split("\n"):
                    if ":" in line:
                        k, _, val = line.partition(":")
                        k = k.strip()
                        val = val.strip().replace(",", "")
                        try:
                            num = float(val)
                            key_candidates.setdefault(k, []).append(num)
                        except ValueError:
                            pass
            # 选条目最多的数值列
            logger.info("数值列候选: %s", {k: len(v) for k, v in key_candidates.items()})
            best_key = None
            best_count = 0
            for k, vals in key_candidates.items():
                if len(vals) > best_count:
                    best_count = len(vals)
                    best_key = k
            if best_key:
                logger.info("自动识别数值列: '%s' (共 %d 条)", best_key, len(key_candidates[best_key]))
                values = key_candidates[best_key]
                key_name = best_key

        return round(sum(values), 2), len(values), values, key_name

    def _filename_fallback(self, question: str, document_ids: list[int] | None = None) -> list:
        """当 embedding 检索无结果时，按文件名关键词匹配回退"""
        # 提取查询中的 2 字以上片段作为关键词
        query_terms = set()
        for i in range(len(question) - 1):
            seg = question[i:i+2]
            if len(seg) == 2:
                query_terms.add(seg.lower())
        if not query_terms:
            return []

        # 获取所有文档名
        doc_names = self.vector_store.get_document_names()

        # 匹配文件名（不区分大小写）
        matched_ids = set()
        for did, fname in doc_names.items():
            fname_lower = fname.lower()
            if any(term in fname_lower for term in query_terms):
                matched_ids.add(did)

        if not matched_ids:
            logger.info("文件名回退: 无匹配文档")
            return []

        # 按前端指定范围筛选
        if document_ids:
            matched_ids &= set(document_ids)

        if not matched_ids:
            return []

        # 拉取匹配文档的全部 chunk，排除汇总行/汇总 sheet
        AGG_CONTENT_KEYWORDS = ["总计", "合计", "小计"]
        filter_expr = {"document_id": {"$in": list(matched_ids)}}
        chunks = self.vector_store.get_all_chunks(filter=filter_expr)
        selected = [
            (doc, 0.0) for doc, _ in chunks
            if doc.metadata.get("sheet_name") != "汇总"
            and not any(kw in doc.page_content for kw in AGG_CONTENT_KEYWORDS)
        ]
        if not selected:
            selected = chunks

        logger.info("文件名回退: 匹配文档 %s, 获取 %d 个 chunk", matched_ids, len(selected))
        return selected

    def answer(self, question: str, top_k: int = None, document_ids: list[int] | None = None,
               history: list[dict] | None = None, strategy: str = None):
        """执行完整 RAG 流程：意图判断 → 查询改写 → 向量检索 → 构建 prompt → LLM 生成"""
        if top_k is None:
            top_k = self.DEFAULT_TOP_K

        # 聚合查询必须走 RAG，跳过意图判断
        is_agg = self._is_aggregation_query(question)

        # 0. 意图判断：通用问题直接回答，跳过检索（聚合查询除外）
        if not is_agg and not self._needs_rag(question):
            history_text = self._format_history(history)
            messages = ChatPromptTemplate.from_template(DIRECT_ANSWER_PROMPT).format_messages(
                question=question, history=history_text
            )
            response = self.llm.invoke(messages)
            return {"answer": response.content, "sources": []}

        # 1. 查询改写：模糊问题结合历史改写为自包含查询
        search_query = self._rewrite_query(question, history)

        # 2. 向量检索
        filter_expr = None
        if document_ids:
            filter_expr = {"document_id": {"$in": document_ids}}

        if is_agg:
            # 聚合查询：先用相似度搜索识别相关文档，再取全量
            if not filter_expr:
                probe_results = self.vector_store.similarity_search(search_query, k=20)
                relevant_ids = list({doc.metadata.get("document_id") for doc, _ in probe_results})
                if relevant_ids:
                    filter_expr = {"document_id": {"$in": relevant_ids}}
                    logger.info("聚合查询: 自动识别相关文档 document_ids=%s", relevant_ids)

            # 取全部 chunk，排除汇总行和汇总 sheet
            AGG_CONTENT_KEYWORDS = ["总计", "合计", "小计"]
            all_chunks = self.vector_store.get_all_chunks(filter=filter_expr)
            selected = [
                (doc, score) for doc, score in all_chunks
                if doc.metadata.get("sheet_name") != "汇总"
                and not any(kw in doc.page_content for kw in AGG_CONTENT_KEYWORDS)
            ]
            if not selected:
                selected = all_chunks
            if not selected:
                return {"answer": "未找到相关文档信息，请尝试其他问题。", "sources": []}
        else:
            results = self.vector_store.similarity_search(
                search_query, k=60, filter=filter_expr
            )
            # 过滤低分 + 根据分数分布动态决定 top_k
            filtered = [(doc, score) for doc, score in results if score <= self.SCORE_THRESHOLD]
            if not filtered:
                fallback_chunks = self._filename_fallback(question, document_ids)
                if not fallback_chunks:
                    return {"answer": "未找到相关文档信息，请尝试其他问题。", "sources": []}
                selected = fallback_chunks
                logger.info("文件名回退成功: 共 %d 个 chunk", len(selected))
            else:
                top_k = self._determine_top_k(filtered)
                # 4. 根据问题动态选择策略
                if strategy is None:
                    strategy = self._select_strategy(question)
                if strategy == "diversity":
                    selected = self._select_by_diversity(filtered, top_k)
                else:
                    selected = filtered[:top_k]

                # 后处理：如果查询关键词出现在未选中文件的文件名或内容中，补入其最佳 chunk
                if len(filtered) > top_k:
                    selected_doc_ids = {doc.metadata.get("document_id") for doc, _ in selected}
                    unmatched = [
                        (doc, score) for doc, score in filtered
                        if doc.metadata.get("document_id") not in selected_doc_ids
                    ]
                    # 提取查询中的 2 字以上片段作为关键词
                    query_terms = set()
                    for i in range(len(question) - 1):
                        seg = question[i:i+2]
                        if len(seg) == 2:
                            query_terms.add(seg.lower())
                    # 每个未选中文档取最佳匹配 chunk（按 score 排序，每个文档最多补1条）
                    best_per_doc = {}
                    for doc_in, score_in in unmatched:
                        fn = doc_in.metadata.get("file_name", "").lower()
                        content = doc_in.page_content.lower()
                        if any(t in fn or t in content for t in query_terms):
                            did = doc_in.metadata.get("document_id")
                            if did not in best_per_doc or score_in < best_per_doc[did][1]:
                                best_per_doc[did] = (doc_in, score_in)
                    for did, (doc_in, score_in) in best_per_doc.items():
                        fn = doc_in.metadata.get("file_name", "")
                        logger.info("内容关键词匹配 → %s (score=%.4f)", fn, score_in)
                        selected.append((doc_in, score_in))

        # 最终按相关性分数升序排列（分数越低越相关）
        selected.sort(key=lambda x: x[1])

        # 调试日志：聚合查询时输出 chunk 来源分布
        agg_precomputed = ""
        if is_agg:
            sheet_counts = {}
            for doc, _ in selected:
                sn = doc.metadata.get("sheet_name", "(无)")
                sheet_counts[sn] = sheet_counts.get(sn, 0) + 1
            logger.info("聚合查询: 共 %d 个 chunk, 来源分布: %s", len(selected), sheet_counts)

            # 两步聚合：先尝试从用户问题中提取指定列名，再 LLM 识别
            target_key = self._extract_column_from_question(question, selected)
            if not target_key:
                target_key = self._identify_target_key(question, selected)
            if target_key:
                total, count, values, actual_key = self._sum_by_key(selected, target_key)
                if count > 0:
                    values_str = " + ".join(str(v) for v in values)
                    agg_precomputed = (
                        f"\n\n【系统精确计算】列\"{actual_key}\"的总和 = {total}（共 {count} 条记录）\n"
                        f"各项数值：{values_str}\n"
                        f"以上数值和总和已由系统精确计算。如果用户要求展示计算过程，请列出各项数值并给出总和；"
                        f"否则直接给出总和即可，不要自行重新计算。"
                    )
                    logger.info("聚合计算: key=%s, 总和=%.2f, 条数=%d", actual_key, total, count)
                else:
                    logger.warning("聚合计算: key='%s' 无法提取数值", target_key)

        # 5. 构建上下文（每段标注来源文件名）
        context_parts = []
        doc_best_scores = {}
        for doc, score in selected:
            source_name = doc.metadata.get("file_name", "未知文档")
            sheet_name = doc.metadata.get("sheet_name")
            if sheet_name:
                source_label = f"{source_name} / {sheet_name}"
            else:
                source_label = source_name
            context_parts.append(f"[来自文件: {source_label}]\n{doc.page_content}")
            uid = doc.metadata.get("document_id", "")
            if uid not in doc_best_scores or score < doc_best_scores[uid]["score"]:
                doc_best_scores[uid] = {
                    "document_id": uid,
                    "file_name": source_name,
                    "content": doc.page_content[:200],
                    "score": score,
                }
        sources = [
            {**v, "score": round(v["score"], 4)}
            for v in doc_best_scores.values()
        ]

        context = "\n\n".join(context_parts)
        if agg_precomputed:
            context += agg_precomputed

        # 6. 构建 Prompt → 调 LLM
        history_text = self._format_history(history)
        messages = self.prompt.format_messages(question=question, context=context, history=history_text)
        try:
            response = self.llm.invoke(messages)
        except Exception:
            return {"answer": "抱歉，生成答案时出现错误，请稍后重试。", "sources": sources}

        return {
            "answer": response.content,
            "sources": sources,
        }
