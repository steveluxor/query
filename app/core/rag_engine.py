import logging

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from app.config import settings
from app.core.vector_store import VectorStore
from app.prompt_manager import PromptManager

logger = logging.getLogger(__name__)


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
        self.prompt = ChatPromptTemplate.from_template(PromptManager.get("rag", "template"))

    # ChromaDB 余弦距离阈值：高于此值视为不相关，排除
    SCORE_THRESHOLD = 0.85
    DEFAULT_TOP_K = 10  # 默认返回 chunk 数量

    MAX_HISTORY_TURNS = 5

    # 摘要压缩：历史超过此轮数时，较早的轮次压缩为摘要
    SUMMARY_COMPRESS_THRESHOLD = 4
    SUMMARY_KEEP_RECENT = 2

    # 间隔阈值：最大间隔低于此值时视为均匀分布，多取结果
    MIN_GAP_THRESHOLD = 0.05

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
        # 分数分布均匀时多取，避免漏掉相关结果
        if max_gap < self.MIN_GAP_THRESHOLD:
            return min(len(filtered), 15)
        return max(3, min(gap_index, 15))

    # 聚合/排名类关键词：需要全量检索而非 Top-K
    AGGREGATION_KEYWORDS = [
        # 聚合
        "总共", "一共", "合计", "总计", "总和", "整个", "所有", "全部",
        "sum", "total", "多少条", "多少个", "有几个", "花了多少", "多少钱",
        # 排名
        "最贵", "最便宜", "最高", "最低", "最大", "最小", "最多", "最少",
        "最好", "最差", "最长", "最短", "最快", "最慢",
        "第一", "第二", "第三", "top",
    ]

    def _is_aggregation_query(self, question: str) -> bool:
        import re
        if any(kw in question for kw in self.AGGREGATION_KEYWORDS):
            return True
        # 匹配 "第N贵"、"第N高" 等模式
        if re.search(r"第\d+[贵便宜高低大小多少差短慢]", question):
            return True
        return False

    def _is_prev_aggregation(self, history: list | None) -> bool:
        """检查上一个问题是否走了聚合路径（通过 history 中的 is_agg 标记判断）"""
        if not history:
            return False
        last = history[-1]
        return last.get("is_agg", False) if isinstance(last, dict) else getattr(last, "is_agg", False)

    RANKING_ASC_KEYWORDS = {"最便宜", "最低", "最小", "最少", "最差", "最短", "最慢"}

    def _is_ranking_query(self, question: str) -> bool:
        import re
        ranking_keywords = [
            "最贵", "最便宜", "最高", "最低", "最大", "最小", "最多", "最少",
            "最好", "最差", "最长", "最短", "最快", "最慢",
            "第一", "第二", "第三", "top",
        ]
        if any(kw in question for kw in ranking_keywords):
            return True
        # 匹配 "第N贵"、"第N高" 等模式
        if re.search(r"第\d+[贵便宜高低大小多少差短慢]", question):
            return True
        return False

    def _parse_rank_position(self, question: str) -> int:
        """从问题中解析排名位置，如"第二贵"→2，"第56"→56，默认1"""
        import re
        match = re.search(r"第(\d+)", question)
        if match:
            return int(match.group(1))
        return 1

    def _extract_row_filter(self, question: str) -> tuple[str, int] | None:
        """从问题中提取行号过滤条件，如"第61行之后"→("ge", 61), "前10行"→("le", 10)"""
        import re
        # 匹配 "第N行之后/以后/后面" → ge N (包含第N行)
        match = re.search(r"第(\d+)行之后|第(\d+)行以后|第(\d+)行后面|(\d+)行之后|(\d+)行以后", question)
        if match:
            row_num = int(match.group(1) or match.group(2) or match.group(3) or match.group(4) or match.group(5))
            return ("ge", row_num)
        # 匹配 "第N行之前/以前/前面" → lt N
        match = re.search(r"第(\d+)行之前|第(\d+)行以前|第(\d+)行前面|(\d+)行之前|(\d+)行以前", question)
        if match:
            row_num = int(match.group(1) or match.group(2) or match.group(3) or match.group(4) or match.group(5))
            return ("lt", row_num)
        # 匹配 "前N行" → le N
        match = re.search(r"前(\d+)行|前(\d+)条", question)
        if match:
            row_num = int(match.group(1) or match.group(2))
            return ("le", row_num)
        # 匹配 "后N行" → ge (总行数-N+1)，但这里简化为返回 None，让 LLM 处理
        return None

    def _rank_by_key(self, chunks: list, key_name: str) -> tuple[list, str]:
        """从所有 chunk 中提取指定 key 的数值，返回全部记录 [(数值, 原文)]"""
        logger.info("排名计算开始: key=%s, chunks数量=%d", key_name, len(chunks))
        records = []
        for doc, _ in chunks:
            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, val = line.partition(":")
                    if k.strip() == key_name:
                        val = val.strip().replace(",", "")
                        try:
                            num = float(val)
                            records.append((num, doc.page_content))
                        except ValueError:
                            if val and val not in ("(空)", "None", ""):
                                logger.debug("排名计算: 值无法转为数字 key='%s', val='%s'", k.strip(), val)

        logger.info("排名计算: 找到 %d 条可排序记录 (共遍历 %d 个chunk)", len(records), len(chunks))

        if not records:
            # 回退：尝试所有数值列
            logger.info("排名计算: 未找到key='%s'的数值，尝试自动识别数值列", key_name)
            key_candidates: dict[str, list[tuple[float, str]]] = {}
            for doc, _ in chunks[:300]:
                for line in doc.page_content.split("\n"):
                    if ":" in line:
                        k, _, val = line.partition(":")
                        k = k.strip()
                        val = val.strip().replace(",", "")
                        try:
                            num = float(val)
                            key_candidates.setdefault(k, []).append((num, doc.page_content))
                        except ValueError:
                            pass
            best_key = max(key_candidates, key=lambda k: len(key_candidates[k]), default=None)
            if best_key:
                logger.info("排名查询自动识别数值列: '%s'", best_key)
                records = key_candidates[best_key]
                key_name = best_key

        return records, key_name

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

    def _summarize_history(self, history: list | None) -> str:
        """历史超过阈值时，将较早轮次压缩为摘要，返回空字符串表示无需压缩"""
        if not history or len(history) < self.SUMMARY_COMPRESS_THRESHOLD:
            return ""
        older = history[:-self.SUMMARY_KEEP_RECENT]
        text = []
        for h in older:
            if isinstance(h, dict):
                q = h.get("question", "")
                a = h.get("answer", "")
            else:
                q = getattr(h, "question", "")
                a = getattr(h, "answer", "")
            text.append(f"用户：{q}")
            text.append(f"助手：{a[:200]}")
        text_str = "\n".join(text)
        prompt = f"""请将以下对话历史压缩为一段简短摘要（30字以内），保留关键信息：已查询的条件、排序方向、文档范围等。

{text_str}

摘要："""
        try:
            response = self.llm.invoke(prompt)
            summary = response.content.strip()
            logger.info("历史压缩: %d 轮→摘要: %s", len(history) - self.SUMMARY_KEEP_RECENT, summary)
            return summary
        except Exception:
            return ""

    def _format_history(self, history: list | None, summary: str = "") -> str:
        """将历史问答格式化为可读文本（兼容 dict 和 HistoryItem）"""
        if not history:
            return ""
        lines = ["<历史对话>"]
        if summary:
            lines.append(f"  [对话摘要] {summary}")
            lines.append("")
            display = history[-self.SUMMARY_KEEP_RECENT:]
        else:
            display = history[-self.MAX_HISTORY_TURNS:]
        for h in display:
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

    def _analyze_intent(self, question: str, history: list | None = None) -> tuple[bool, str]:
        """一次 LLM 调用同时判断是否需要检索和选块策略"""
        last_question = ""
        if history:
            last = history[-1]
            last_question = last.get("question", "") if isinstance(last, dict) else getattr(last, "question", "")
        prompt = PromptManager.get("intent", "analysis").format(question=question, last_question=last_question)
        try:
            response = self.llm.invoke(prompt)
            parts = response.content.strip().split(",")
            needs_rag = "不需要" not in parts[0]
            strategy = "diversity" if len(parts) > 1 and "diversity" in parts[1].strip().lower() else "relevance"
            return needs_rag, strategy
        except Exception:
            return True, "relevance"


    def _rewrite_query(self, question: str, history: list[dict] | None, history_summary: str = "") -> str:
        """将问题改写为更利于检索的查询——无论有无历史都会改写"""
        if history:
            history_text = self._format_history(history, history_summary)
            prompt = PromptManager.get("rewrite", "with_history").format(history=history_text, question=question)
        else:
            prompt = PromptManager.get("rewrite", "without_history").format(question=question)
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
        # 排除的key（元数据列，不是数据列）
        exclude_keys = {"行号", "sheet_name", "file_name", "document_id", "chunk_index", "source"}
        for doc, _ in chunks[:300]:
            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, val = line.partition(":")
                    k = k.strip()
                    # 跳过元数据列
                    if k in exclude_keys:
                        continue
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
        # 排名/聚合查询的语义匹配：根据问题关键词推断目标列
        PRICE_KEYWORDS = {"贵", "便宜", "价格", "原价", "成交", "花费", "费用", "金额", "总价"}
        SUM_KEYWORDS = {"总", "合计", "一共", "多少", "几", "数量", "个数"}
        # 特殊术语 → 列名映射（用户说"到手价"应匹配"结果"列）
        TERM_TO_COLUMN = {"到手价": "结果", "到手": "结果", "实付": "结果", "实付价": "结果"}
        for term, col in TERM_TO_COLUMN.items():
            if term in question and col in numeric_keys:
                logger.info("术语映射: '%s' → 列'%s'", term, col)
                return col
        if any(kw in question for kw in PRICE_KEYWORDS):
            # 优先匹配价格相关列名
            price_candidates = [k for k in numeric_keys if any(pk in k for pk in ["价", "价格", "原价", "结果", "成交", "花费"])]
            if price_candidates:
                best = max(price_candidates, key=len)
                logger.info("价格语义匹配列名: '%s'", best)
                return best
        if any(kw in question for kw in SUM_KEYWORDS):
            # 聚合查询，选条目最多的数值列
            best = max(numeric_keys, key=lambda k: len(key_values[k]))
            logger.info("聚合语义匹配列名: '%s'", best)
            return best
        return None

    def _identify_target_key(self, question: str, chunks: list) -> str | None:
        """让 LLM 从原始 chunk 内容中判断要加总的 key 名"""
        if not chunks:
            return None
        # 发前 5 个 chunk 的原始内容
        sample = "\n---\n".join(doc.page_content for doc, _ in chunks[:5])
        prompt = PromptManager.get("identify_key", "template").format(sample=sample, question=question)
        try:
            response = self.llm.invoke(prompt)
            key = response.content.strip().strip("'\"")
            logger.info("列识别: LLM返回key='%s'", key)
            return key
        except Exception:
            pass
        return None

    def _sum_by_key(self, chunks: list, key_name: str, row_filter: tuple[str, int] | None = None) -> tuple[float, int, list[tuple[int, float]], str]:
        """从所有 chunk 中提取指定 key 的数值并求和，找不到数值时自动回退
        row_filter: (op, row_num) 其中 op 为 "gt"/"lt"/"le"/"ge"
        返回: (总和, 数量, [(行号, 数值)], 列名)
        """
        values = []  # [(行号, 数值)]
        for doc, _ in chunks:
            # 提取行号
            row_num = doc.metadata.get("row_number")
            # 检查行号过滤条件
            if row_filter and row_num is not None:
                op, filter_row = row_filter
                if op == "gt" and row_num <= filter_row:
                    continue
                elif op == "lt" and row_num >= filter_row:
                    continue
                elif op == "le" and row_num > filter_row:
                    continue
                elif op == "ge" and row_num < filter_row:
                    continue

            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, val = line.partition(":")
                    if k.strip() == key_name:
                        val = val.strip().replace(",", "")
                        try:
                            num_val = float(val)
                            # 使用行号，如果没有则用 -1
                            values.append((row_num if row_num is not None else -1, num_val))
                        except ValueError:
                            pass

        # 回退：如果识别的 key 无法提取数值，尝试所有 key 找到第一个能求和的
        if not values:
            logger.warning("列'%s'无法提取数值，尝试自动识别数值列", key_name)
            key_candidates: dict[str, list[tuple[int, float]]] = {}
            # 排除的key（元数据列，不是数据列）
            exclude_keys = {"行号", "sheet_name", "file_name", "document_id", "chunk_index", "source"}
            for doc, _ in chunks[:300]:
                # 提取行号
                row_num = doc.metadata.get("row_number")
                # 检查行号过滤条件
                if row_filter and row_num is not None:
                    op, filter_row = row_filter
                    if op == "gt" and row_num <= filter_row:
                        continue
                    elif op == "lt" and row_num >= filter_row:
                        continue
                    elif op == "le" and row_num > filter_row:
                        continue
                    elif op == "ge" and row_num < filter_row:
                        continue

                for line in doc.page_content.split("\n"):
                    if ":" in line:
                        k, _, val = line.partition(":")
                        k = k.strip()
                        # 跳过元数据列
                        if k in exclude_keys:
                            continue
                        val = val.strip().replace(",", "")
                        try:
                            num = float(val)
                            key_candidates.setdefault(k, []).append(
                                (row_num if row_num is not None else -1, num)
                            )
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

        total = round(sum(v for _, v in values), 2)
        return total, len(values), values, key_name

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
        """执行完整 RAG 流程：查询改写 → 聚合判断 → 意图分析 → 检索 → LLM 生成"""
        if top_k is None:
            top_k = self.DEFAULT_TOP_K

        # 0. 预计算历史摘要（超过阈值时压缩较早轮次）
        history_summary = self._summarize_history(history)

        # 1. 查询改写：模糊问题结合历史改写为自包含查询（必须先于 is_agg 判断）
        search_query = self._rewrite_query(question, history, history_summary)
        logger.info("查询改写: 原始='%s' → 改写='%s'", question, search_query)

        # 2. 用改写后的查询判断是否聚合查询（关键词规则，无需 LLM）
        is_agg = self._is_aggregation_query(search_query)
        logger.info("意图分析: is_agg=%s, search_query='%s'", is_agg, search_query[:80])

        # 3. 非聚合查询才走 LLM 意图分析
        if not is_agg:
            needs_rag, detected_strategy = self._analyze_intent(question, history)
            if not needs_rag:
                logger.info("意图分析: 无需检索，直接回答")
                history_text = self._format_history(history, history_summary)
                messages = ChatPromptTemplate.from_template(PromptManager.get("direct_answer", "template")).format_messages(
                    question=question, history=history_text
                )
                response = self.llm.invoke(messages)
                return {"answer": response.content, "sources": [], "is_agg": False}
            if strategy is None:
                strategy = detected_strategy

        # 4. 向量检索
        filter_expr = None
        if document_ids:
            filter_expr = {"document_id": {"$in": document_ids}}

        if is_agg:
            # 聚合查询：先用相似度搜索识别相关文档，再取全量
            filtered = None  # 聚合查询不需要 filtered，排名直接用 selected（全量）
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
                return {"answer": "未找到相关文档信息，请尝试其他问题。", "sources": [], "is_agg": is_agg}
        else:
            results = self.vector_store.similarity_search(
                search_query, k=60, filter=filter_expr
            )
            # 过滤低分 + 根据分数分布动态决定 top_k
            filtered = [(doc, score) for doc, score in results if score <= self.SCORE_THRESHOLD]
            if not filtered:
                fallback_chunks = self._filename_fallback(question, document_ids)
                if not fallback_chunks:
                    return {"answer": "未找到相关文档信息，请尝试其他问题。", "sources": [], "is_agg": is_agg}
                selected = fallback_chunks
                logger.info("文件名回退成功: 共 %d 个 chunk", len(selected))
            else:
                top_k = self._determine_top_k(filtered)
                # 根据问题动态选择策略
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
                target_key = self._identify_target_key(search_query, selected)

            # 排名查询：精确计算极值（用改写后的 search_query 判断，因为原始问题可能太简短）
            rank_pool = selected if is_agg else filtered
            is_rank = self._is_ranking_query(search_query)
            if is_rank and target_key:
                ascending = any(kw in search_query for kw in self.RANKING_ASC_KEYWORDS)
                if not ascending:
                    import re
                    ascending = bool(re.search(r"第\d+[便宜低小少差短慢]", search_query))
                rank_pos = self._parse_rank_position(search_query)
                logger.info("排名查询: target_key=%s, ascending=%s, rank_pos=%d, 候选池=%d", target_key, ascending, rank_pos, len(rank_pool))

                # 计算排名
                all_records, actual_key = self._rank_by_key(rank_pool, target_key)
                all_records.sort(key=lambda x: x[0], reverse=not ascending)
                logger.info("排名计算完成: 总记录数=%d, 排序方向=%s", len(all_records), "升序" if ascending else "降序")
                if rank_pos <= len(all_records):
                    rank_value, rank_text = all_records[rank_pos - 1]
                else:
                    rank_value, rank_text = None, None

                if rank_value is not None:
                    order_desc = "升序第" + str(rank_pos) if ascending else "降序第" + str(rank_pos)
                    agg_precomputed = (
                        f"\n\n【系统精确计算】列\"{actual_key}\"排序后{order_desc}位：\n"
                        f"数值：{rank_value}\n"
                        f"完整记录：{rank_text}\n"
                        f"以上结果已由系统精确计算，请直接引用此结果回答。"
                    )
                    logger.info("排名结果: key=%s, %s, 值=%.2f", actual_key, order_desc, rank_value)
                    logger.info("排名完整记录: %s", rank_text[:200])
            # 聚合查询：求和
            elif target_key:
                # 提取行号过滤条件
                row_filter = self._extract_row_filter(question)
                if row_filter:
                    logger.info("行号过滤: op=%s, row=%d", row_filter[0], row_filter[1])
                total, count, values, actual_key = self._sum_by_key(rank_pool, target_key, row_filter)
                if count > 0:
                    # 生成带行号的详细列表
                    details = []
                    for row_num, val in values:
                        if row_num > 0:
                            details.append(f"第{row_num}行: {val}")
                        else:
                            details.append(f"{val}")
                    details_str = "、".join(details)
                    # 生成过滤条件描述
                    filter_desc = ""
                    if row_filter:
                        op, row_num = row_filter
                        if op == "gt":
                            filter_desc = f"（第{row_num}行之后）"
                        elif op == "lt":
                            filter_desc = f"（第{row_num}行之前）"
                        elif op == "le":
                            filter_desc = f"（前{row_num}行）"
                        elif op == "ge":
                            filter_desc = f"（第{row_num}行及之后）"
                    agg_precomputed = (
                        f"\n\n【系统精确计算】列\"{actual_key}\"的总和 = {total}（共 {count} 条记录{filter_desc}）\n"
                        f"详细数据：{details_str}\n"
                        f"计算公式：{' + '.join(str(v) for _, v in values)} = {total}\n"
                        f"以上数据已由系统精确计算。如果用户要求展示计算过程，请直接引用此详细数据，不要自行编造。"
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
        history_text = self._format_history(history, history_summary)
        messages = self.prompt.format_messages(question=question, context=context, history=history_text)
        try:
            response = self.llm.invoke(messages)
        except Exception:
            return {"answer": "抱歉，生成答案时出现错误，请稍后重试。", "sources": sources, "is_agg": is_agg}

        return {
            "answer": response.content,
            "sources": sources,
            "is_agg": is_agg,
        }
