import logging
import re
from dataclasses import dataclass, field

from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

from app.config import settings
from app.core.vector_store import VectorStore

logger = logging.getLogger(__name__)


@dataclass
class SearchContext:
    """搜索上下文（Knowledge Agent 使用，兼容 /qa/ask 旧流程）"""
    last_search_chunks: list = field(default_factory=list)
    last_search_filtered: list = field(default_factory=list)
    last_search_all_chunks: list = field(default_factory=list)
    last_search_sources: list = field(default_factory=list)
    last_search_query: str = ""
    has_aggregation: bool = False
    search_count: int = 0
    agg_count: int = 0
    document_ids: list[int] | None = None
    tools_called: list[str] = field(default_factory=list)


@dataclass
class AnalysisContext:
    """计算上下文（Analysis Agent 使用），接收搜索结果作为输入"""
    chunks: list = field(default_factory=list)
    filtered: list = field(default_factory=list)
    all_chunks: list = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    has_aggregation: bool = False
    agg_count: int = 0
    document_ids: list[int] | None = None
    tools_called: list[str] = field(default_factory=list)


class RAGEngine:
    """RAG 引擎：通过 Tool Calling 驱动问答流程"""

    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self.llm = ChatOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model_name,
            temperature=0.1,
            max_tokens=4096,
            timeout=30,
        )

    # ChromaDB 余弦距离阈值：高于此值视为不相关，排除
    SCORE_THRESHOLD = 0.92
    DEFAULT_TOP_K = 10

    MAX_HISTORY_TURNS = 5
    MIN_GAP_THRESHOLD = 0.05

    # ==================== 上下文兼容辅助 ====================

    @staticmethod
    def _ctx_chunks(ctx) -> list:
        """从 SearchContext 或 AnalysisContext 获取 chunks"""
        return getattr(ctx, 'last_search_chunks', None) or getattr(ctx, 'chunks', [])

    @staticmethod
    def _ctx_filtered(ctx) -> list:
        return getattr(ctx, 'last_search_filtered', None) or getattr(ctx, 'filtered', [])

    @staticmethod
    def _ctx_all_chunks(ctx) -> list:
        return getattr(ctx, 'last_search_all_chunks', None) or getattr(ctx, 'all_chunks', [])

    @staticmethod
    def _ctx_set_all_chunks(ctx, value):
        if hasattr(ctx, 'last_search_all_chunks'):
            ctx.last_search_all_chunks = value
        else:
            ctx.all_chunks = value

    # ==================== 工具定义 ====================

    def _create_search_tools(self, ctx):
        """搜索工具：search_documents, list_documents"""

        @tool
        def search_documents(query: str, row_start: int | None = None, row_end: int | None = None) -> str:
            """从知识库中搜索与问题相关的文档内容。需要查找具体信息、数据、记录时调用。搜索词应具体，包含数据中可能的列名。
            如果要查询特定行号范围（如"第90到100行"、"第91行之后"），请传入 row_start 和 row_end 参数。"""
            ctx.tools_called.append("search_documents")
            ctx.search_count += 1
            if ctx.search_count > 2:
                logger.warning("搜索次数超限，拒绝第 %d 次搜索(query='%s')", ctx.search_count, query)
                return (
                    "你已经搜索两次了。请基于已获得的数据，"
                    "直接回答或调用 calculate_sum/calculate_rank 进行精确计算。"
                )
            return self._execute_search(query, row_start, row_end, ctx)

        @tool
        def list_documents() -> str:
            """列出当前知识库中可检索的文档数量和名称。当用户问"有多少文件"、"能搜到几个文档"、"有哪些文档"等元信息问题时调用。"""
            ctx.tools_called.append("list_documents")
            all_names = self.vector_store.get_document_names()
            if ctx.document_ids:
                matched = {did: all_names[did] for did in ctx.document_ids if did in all_names}
            else:
                matched = all_names
            if not matched:
                return "当前知识库中没有可检索的文档。"
            lines = [f"共 {len(matched)} 个文档："]
            for did, name in sorted(matched.items()):
                lines.append(f"- [{did}] {name}")
            return "\n".join(lines)

        return [search_documents, list_documents]

    def _create_analysis_tools(self, ctx):
        """计算工具：calculate_sum, calculate_rank, read_all_rows
        ctx 可以是 SearchContext 或 AnalysisContext，只要有 chunks/filtered/all_chunks 字段即可。"""

        @tool
        def calculate_sum(key_name: str, row_filter: str = "", content_filter: str = "") -> str:
            """对已检索到的文档内容中指定列（key）的数值进行精确求和。当用户问"总共"、"合计"、"一共多少钱"等加总问题时调用。必须先调用 search_documents 获取数据后才能使用此工具。
            content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的行求和。"""
            ctx.tools_called.append("calculate_sum")
            ctx.agg_count += 1
            if ctx.agg_count > 8:
                logger.warning("计算工具调用超限，拒绝第 %d 次调用", ctx.agg_count)
                return (
                    "计算工具调用已达上限（最多8次）。"
                    "请基于已获得的数据直接回答。"
                )
            return self._execute_sum(key_name, row_filter, content_filter, ctx)

        @tool
        def calculate_rank(key_name: str, ascending: bool, position: int = 1, content_filter: str = "") -> str:
            """从已检索到的文档内容中，对指定列（key）的数值排序并返回第N名的记录。当用户问"最贵"、"最便宜"、"第三高"等排名问题时调用。ascending=true=升序(最便宜/最低)，false=降序(最贵/最高)。必须先调用 search_documents 获取数据后才能使用此工具。
            content_filter: 可选，按内容过滤，格式为"列名=值"，如"品牌=万代"只对品牌为万代的记录排序。"""
            ctx.tools_called.append("calculate_rank")
            ctx.agg_count += 1
            if ctx.agg_count > 8:
                logger.warning("计算工具调用超限，拒绝第 %d 次调用", ctx.agg_count)
                return (
                    "计算工具调用已达上限（最多8次）。"
                    "请基于已获得的数据直接回答。"
                )
            return self._execute_rank(key_name, ascending, position, content_filter, ctx)

        @tool
        def read_all_rows() -> str:
            """读取当前搜索到的文档的全部数据行。当需要完整信息（如列出所有品牌、所有记录、完整清单）时调用。当前 search_documents 只返回部分数据，调用此工具可获取全文。必须先调用 search_documents 才能使用。"""
            ctx.tools_called.append("read_all_rows")
            return self._execute_read_all_rows(ctx)

        return [calculate_sum, calculate_rank, read_all_rows]

    # ==================== 搜索与结果处理 ====================

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
        if max_gap < self.MIN_GAP_THRESHOLD:
            return min(len(filtered), 15)
        return max(3, min(gap_index, 15))

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

    @staticmethod
    def _bigrams(text: str) -> set[str]:
        """生成字符串的二元分词集合"""
        return {text[i:i+2].lower() for i in range(len(text) - 1) if len(text[i:i+2]) == 2}

    @staticmethod
    def _extract_chinese(text: str) -> set[str]:
        """提取字符串中的所有中文字符"""
        return {ch for ch in text if '一' <= ch <= '鿿'}

    # 查询中的停用词（常见动词/助词/代词，不作为搜索关键词）
    _STOP_WORDS = set("的了是在我有和人这中大为上个国我以要他时来用们生到作地于出会"

                      "那就得着不没好能说过也后前里向下回己点起把让"

                      "干做搞什么怎样如何哪些哪里谁几"

                      "吗吧啊呢哦哈呀嗯")

    @classmethod
    def _extract_keywords(cls, text: str) -> list[str]:
        """从查询中提取搜索关键词，使用 jieba 分词"""
        import jieba
        words = jieba.cut(text)
        keywords = []
        for w in words:
            w = w.strip()
            if len(w) < 2:
                continue
            if w in cls._STOP_WORDS:
                continue
            keywords.append(w)
        return keywords

    def _filename_fallback(self, question: str, document_ids: list[int] | None = None) -> list:
        """当 embedding 检索无结果时，按文件名关键词匹配回退（优先 bigram，无匹配则降级到单字重叠）"""
        query_bigrams = self._bigrams(question)
        query_chars = self._extract_chinese(question)

        doc_names = self.vector_store.get_document_names()

        scored_matches: list[tuple[int, float]] = []

        for did, fname in doc_names.items():
            name_stem = fname.rsplit(".", 1)[0].lower()

            # 优先 bigram 匹配
            if query_bigrams:
                name_bigrams = self._bigrams(name_stem)
                bigram_overlap = query_bigrams & name_bigrams
                if bigram_overlap:
                    score = len(bigram_overlap) / max(len(name_bigrams), 1)
                    scored_matches.append((did, score))
                    logger.info("文件名回退(bigram): 文档 %d '%s' 匹配 %s (score=%.2f)",
                                did, fname, bigram_overlap, score)
                    continue

            # 降级到单字重叠匹配
            name_chars = self._extract_chinese(name_stem)
            if name_chars:
                char_overlap = query_chars & name_chars
                if char_overlap:
                    score = len(char_overlap) / len(name_chars) * 0.5
                    scored_matches.append((did, score))
                    logger.info("文件名回退(单字): 文档 %d '%s' 匹配中文字符 %s (score=%.2f)",
                                did, fname, char_overlap, score)

        if not scored_matches:
            logger.info("文件名回退: 无匹配文档")
            return []

        scored_matches.sort(key=lambda x: -x[1])
        matched_ids = {did for did, _ in scored_matches}

        if document_ids:
            matched_ids &= set(document_ids)

        if not matched_ids:
            return []

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

    # ==================== 聚合/排名辅助 ====================

    def _parse_row_filter(self, row_filter_str: str) -> tuple[str, int] | None:
        """解析行过滤字符串，如'前10行'→('le',10), '第5行之后'→('ge',5)"""
        if not row_filter_str:
            return None
        m = re.search(r"前(\d+)行", row_filter_str)
        if m:
            return ("le", int(m.group(1)))
        m = re.search(r"第(\d+)行(?:之后|以后|后面)", row_filter_str)
        if m:
            return ("ge", int(m.group(1)))
        m = re.search(r"第(\d+)行(?:之前|以前|前面)", row_filter_str)
        if m:
            return ("lt", int(m.group(1)))
        return None

    @staticmethod
    def _parse_content_filter(content_filter: str) -> tuple[str, str] | None:
        """解析内容过滤字符串，如'品牌=万代'→('品牌','万代')"""
        if not content_filter:
            return None
        m = re.search(r"(.+?)=\s*(.+)", content_filter)
        if m:
            return m.group(1).strip(), m.group(2).strip()
        return None

    @staticmethod
    def _filter_chunks_by_content(chunks: list, key: str, value: str) -> list:
        """按内容过滤 chunk，只保留 page_content 中包含 'key: value' 的行"""
        result = []
        for doc, score in chunks:
            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, v = line.partition(":")
                    if k.strip() == key and v.strip() == value:
                        result.append((doc, score))
                        break
        return result

    EXCLUDE_KEYS = {"行号", "sheet_name", "file_name", "document_id", "chunk_index", "source"}

    # 用于判断记录是否有实质内容的关键字段
    EMPTY_CONTENT_KEYS = {"产品名", "品牌", "内容", "描述", "摘要", "标题", "姓名", "作者"}

    @staticmethod
    def _is_empty_record(content: str) -> bool:
        """检查 chunk 是否为空记录（所有标识类字段均为空值），排名/求和中跳过"""
        for line in content.split("\n"):
            if ":" in line:
                k, _, val = line.partition(":")
                k, val = k.strip(), val.strip()
                if k in RAGEngine.EMPTY_CONTENT_KEYS and val not in ("(空)", "", "None"):
                    return False
        return True

    def _rank_by_key(self, chunks: list, key_name: str) -> tuple[list, str]:
        """从所有 chunk 中提取指定 key 的数值，返回全部记录 [(数值, 原文)]，跳过空记录"""
        records = []
        for doc, _ in chunks:
            if self._is_empty_record(doc.page_content):
                continue
            for line in doc.page_content.split("\n"):
                if ":" in line:
                    k, _, val = line.partition(":")
                    if k.strip() == key_name:
                        val = val.strip().replace(",", "")
                        try:
                            num = float(val)
                            records.append((num, doc.page_content))
                        except ValueError:
                            pass
        if not records:
            key_candidates: dict[str, list[tuple[float, str]]] = {}
            for doc, _ in chunks[:300]:
                for line in doc.page_content.split("\n"):
                    if ":" in line:
                        k, _, val = line.partition(":")
                        k = k.strip()
                        if k in self.EXCLUDE_KEYS:
                            continue
                        val = val.strip().replace(",", "")
                        try:
                            num = float(val)
                            key_candidates.setdefault(k, []).append((num, doc.page_content))
                        except ValueError:
                            pass
            best_key = max(key_candidates, key=lambda k: len(key_candidates[k]), default=None)
            if best_key:
                records = key_candidates[best_key]
                key_name = best_key
        return records, key_name

    def _sum_by_key(self, chunks: list, key_name: str, row_filter: tuple[str, int] | None = None) -> tuple[float, int, list[tuple[int, float]], str]:
        """从所有 chunk 中提取指定 key 的数值并求和，找不到数值时自动回退"""
        values: list[tuple[int, float]] = []
        for doc, _ in chunks:
            row_num = doc.metadata.get("row_number")
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
                            values.append((row_num if row_num is not None else -1, float(val)))
                        except ValueError:
                            pass
        if not values:
            key_candidates: dict[str, list[tuple[int, float]]] = {}
            for doc, _ in chunks[:300]:
                row_num = doc.metadata.get("row_number")
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
                        if k in self.EXCLUDE_KEYS:
                            continue
                        val = val.strip().replace(",", "")
                        try:
                            key_candidates.setdefault(k, []).append(
                                (row_num if row_num is not None else -1, float(val))
                            )
                        except ValueError:
                            pass
            best_key = max(key_candidates, key=lambda k: len(key_candidates[k]), default=None)
            if best_key:
                values = key_candidates[best_key]
                key_name = best_key
        total = round(sum(v for _, v in values), 2)
        return total, len(values), values, key_name

    # ==================== Tool 执行体 ====================

    def _execute_search(self, query: str, row_start: int | None = None, row_end: int | None = None,
                        ctx: SearchContext | None = None) -> str:
        """执行向量搜索，返回格式化后的文档内容供 LLM 读取，同时缓存原始结果供聚合工具使用"""
        logger.info("执行搜索: query='%s', row_start=%s, row_end=%s", query, row_start, row_end)
        ctx = ctx or SearchContext()
        ctx.last_search_query = query
        ctx.has_aggregation = False

        doc_filter = {"document_id": {"$in": ctx.document_ids}} if ctx.document_ids else None

        # 如果 LLM 指定了行号范围，直接按 row_number 元数据过滤
        if row_start is not None or row_end is not None:
            start = row_start or 1
            end = row_end or 999999
            all_chunks = self.vector_store.get_all_chunks(filter=doc_filter)
            selected = [
                (doc, 0.0) for doc, _ in all_chunks
                if doc.metadata.get("row_number") is not None
                and start <= doc.metadata["row_number"] <= end
            ]
            if selected:
                selected.sort(key=lambda x: x[0].metadata.get("row_number", 0))
                filtered = []
                # 检查是否返回了完整范围的数据
                found_rows = {doc.metadata.get("row_number") for doc, _ in selected}
                expected_rows = set(range(start, end + 1))
                missing_rows = expected_rows - found_rows
                if missing_rows:
                    logger.info("行号范围查询(LLM指定): %d~%d, 共 %d 个 chunk, 缺失行: %s",
                                start, end, len(selected), sorted(missing_rows)[:10])
                else:
                    logger.info("行号范围查询(LLM指定): %d~%d, 共 %d 个 chunk (完整)", start, end, len(selected))
            else:
                logger.info("行号范围查询: 未找到 %d~%d 范围内的数据", start, end)
                return f"未找到行号 {start}~{end} 范围内的数据。"
        else:
            # 提取关键词用于搜索和验证
            keywords = self._extract_keywords(query)
            logger.info("提取关键词: %s", keywords)

            # 1. Embedding 相似度搜索
            results = self.vector_store.similarity_search(query, k=60, filter=doc_filter)
            filtered = [(doc, score) for doc, score in results if score <= self.SCORE_THRESHOLD]

            # 2. 关键词搜索（补充 embedding 可能遗漏的文档）
            keyword_results = []
            if keywords:
                keyword_results = self.vector_store.keyword_search(keywords, filter=doc_filter)

            # 3. 合并结果：关键词匹配的文档优先补入
            if keyword_results:
                selected_doc_ids = {doc.metadata.get("document_id") for doc, _ in filtered} if filtered else set()
                for doc, kw_score in keyword_results:
                    did = doc.metadata.get("document_id")
                    # 如果是新文档，补入（用较低的 score 确保排在前面）
                    if did not in selected_doc_ids:
                        filtered.append((doc, 0.3))  # 关键词匹配给予较好分数
                        selected_doc_ids.add(did)
                        logger.info("关键词搜索补入: %s (doc_id=%s)", doc.metadata.get("file_name", ""), did)

            if not filtered:
                fallback_chunks = self._filename_fallback(query, ctx.document_ids)
                if not fallback_chunks:
                    return "未找到相关文档内容，请尝试其他搜索关键词。"
                selected = fallback_chunks
                filtered = []
                logger.info("文件名回退: 共 %d 个 chunk", len(selected))
            else:
                # 4. 验证：过滤掉不含关键词的假阳性（仅在有明确关键词时）
                if keywords:
                    verified = []
                    for doc, score in filtered:
                        content_lower = doc.page_content.lower()
                        if any(kw.lower() in content_lower for kw in keywords):
                            verified.append((doc, score))
                        else:
                            logger.info("假阳性过滤: %s (不含关键词 %s)", doc.metadata.get("file_name", ""), keywords)
                    if verified:
                        filtered = verified

                actual_top_k = self._determine_top_k(filtered)
                if actual_top_k >= len(filtered):
                    selected = filtered
                else:
                    selected = self._select_by_diversity(filtered, actual_top_k)

                # 文件名补充：embedding 漏掉的文档
                fallback_chunks = self._filename_fallback(query, ctx.document_ids)
                if fallback_chunks:
                    selected_ids = {doc.metadata.get("document_id") for doc, _ in selected}
                    for doc, _ in fallback_chunks:
                        did = doc.metadata.get("document_id")
                        if did not in selected_ids:
                            selected.append((doc, 1.0))
                            selected_ids.add(did)
                            logger.info("文件名补充(embedding漏掉): %s", doc.metadata.get("file_name", ""))

        selected.sort(key=lambda x: x[1])
        ctx.last_search_chunks = selected
        ctx.last_search_filtered = filtered
        ctx.last_search_all_chunks = []  # 搜索新内容时清除缓存，确保后续全量加载使用最新搜索的文档

        # 如果 LLM 指定了行号范围，从已选数据中确保该范围内的行都在 selected 中
        if row_start is not None or row_end is not None:
            start = row_start or 1
            end = row_end or 999999
            existing_rows = {doc.metadata.get("row_number") for doc, _ in selected if doc.metadata.get("row_number")}
            # 可能有些行在 _last_search_chunks 中但不在 selected 的返回集里
            pool = ctx.last_search_chunks or ctx.last_search_filtered
            for doc, score in pool:
                rn = doc.metadata.get("row_number")
                if rn and start <= rn <= end and rn not in existing_rows:
                    selected.append((doc, 0.0))
                    existing_rows.add(rn)

        # 行号范围模式下，再从全量数据中补入缺失的行
        if row_start is not None or row_end is not None:
            start = row_start or 1
            end = row_end or 999999
            existing_rows = {doc.metadata.get("row_number") for doc, _ in selected}
            for doc, score in self._load_all_chunks(ctx):
                rn = doc.metadata.get("row_number")
                if rn and start <= rn <= end and rn not in existing_rows:
                    selected.append((doc, 0.0))
                    existing_rows.add(rn)
                    logger.info("行号范围补充: row=%d", rn)

        # 提取 sources
        doc_best_scores = {}
        for doc, score in selected:
            uid = doc.metadata.get("document_id", "")
            source_name = doc.metadata.get("file_name", "未知文档")
            if uid not in doc_best_scores or score < doc_best_scores[uid]["score"]:
                doc_best_scores[uid] = {
                    "document_id": uid,
                    "file_name": source_name,
                    "content": doc.page_content[:200],
                    "score": score,
                }
        ctx.last_search_sources = [
            {**v, "score": round(v["score"], 4)} for v in doc_best_scores.values()
        ]

        # 构建返回给 LLM 的文本
        context_parts = []
        for doc, score in selected:
            source_name = doc.metadata.get("file_name", "未知文档")
            sheet_name = doc.metadata.get("sheet_name")
            label = f"{source_name} / {sheet_name}" if sheet_name else source_name
            context_parts.append(f"[{label}]\n{doc.page_content}")
        result = f"检索到以下相关内容：\n\n" + "\n\n".join(context_parts)

        # 检查数据完整性，提示 LLM 是否需要调用 read_all_rows
        is_incomplete = False
        if row_start is not None and row_end is not None:
            # 行号范围查询：检查是否返回了完整范围
            found_rows = {doc.metadata.get("row_number") for doc, _ in selected}
            expected_count = row_end - row_start + 1
            if len(found_rows) < expected_count:
                is_incomplete = True
        elif row_start is None and row_end is None:
            # 普通搜索：总是提示可能不完整
            is_incomplete = True

        if is_incomplete:
            result += (
                "\n\n【重要】以上只显示了部分数据。"
                "你必须立即调用 read_all_rows 工具获取完整数据，不要跳过此步骤。"
                "在获取完整数据之前，不要生成最终回答。"
            )

        logger.info("search_documents 返回 %d 个 chunk", len(selected))
        return result

    def _execute_sum(self, key_name: str, row_filter: str = "", content_filter: str = "",
                     ctx=None) -> str:
        """对已检索结果执行求和，返回格式化计算结果"""
        ctx = ctx or SearchContext()
        ctx.has_aggregation = True
        chunks = self._ctx_chunks(ctx)
        if not chunks:
            return "没有可计算的数据，请先调用 search_documents 搜索相关内容。"
        logger.info("执行求和: key_name='%s', row_filter='%s', content_filter='%s'", key_name, row_filter, content_filter)

        rf = self._parse_row_filter(row_filter) if row_filter else None
        pool = self._load_all_chunks(ctx) or self._ctx_filtered(ctx) or chunks

        # 内容过滤
        cf = self._parse_content_filter(content_filter)
        if cf:
            cf_key, cf_value = cf
            pool = self._filter_chunks_by_content(pool, cf_key, cf_value)
            logger.info("内容过滤: %s=%s, 剩余 %d 个 chunk", cf_key, cf_value, len(pool))

        total, count, values, actual_key = self._sum_by_key(pool, key_name, rf)
        if count == 0:
            return f"列'{key_name}'中未找到可求和的数值数据。"

        details = []
        for row_num, val in values:
            if row_num > 0:
                details.append(f"第{row_num}行: {val}")
            else:
                details.append(f"{val}")

        filter_desc = ""
        if rf:
            op, row_num = rf
            mapping = {("le",): f"（前{row_num}行）", ("ge",): f"（第{row_num}行及之后）", ("lt",): f"（第{row_num}行之前）"}
            filter_desc = mapping.get((op,), "")

        result = (
            f"【系统精确计算结果】\n"
            f"列\"{actual_key}\"的总和 = {total}（共 {count} 条记录{filter_desc}）\n"
            f"详细数据：{'、'.join(details)}\n"
            f"公式：{' + '.join(str(v) for _, v in values)} = {total}\n"
            f"以上数据已由系统精确计算，请直接引用。"
        )
        logger.info("求和结果: %s", result[:200])
        return result

    def _execute_rank(self, key_name: str, ascending: bool, position: int = 1, content_filter: str = "",
                      ctx=None) -> str:
        """对已检索结果执行排序，返回格式化排名结果"""
        ctx = ctx or SearchContext()
        ctx.has_aggregation = True
        chunks = self._ctx_chunks(ctx)
        if not chunks:
            return "没有可计算的数据，请先调用 search_documents 搜索相关内容。"
        logger.info("执行排名: key_name='%s', ascending=%s, position=%d, content_filter='%s'", key_name, ascending, position, content_filter)

        pool = self._load_all_chunks(ctx) or self._ctx_filtered(ctx) or chunks
        # 内容过滤
        cf = self._parse_content_filter(content_filter)
        if cf:
            cf_key, cf_value = cf
            pool = self._filter_chunks_by_content(pool, cf_key, cf_value)
            logger.info("内容过滤: %s=%s, 剩余 %d 个 chunk", cf_key, cf_value, len(pool))

        records, actual_key = self._rank_by_key(pool, key_name)
        records.sort(key=lambda x: x[0], reverse=not ascending)

        if not records:
            return f"列'{key_name}'中未找到可排序的数值数据。"

        order_desc = "升序" if ascending else "降序"
        logger.info("排名计算: key=%s, %s, 总记录=%d", actual_key, order_desc, len(records))

        if position <= len(records):
            rank_value, rank_text = records[position - 1]
            result = (
                f"【系统精确计算结果】\n"
                f"列\"{actual_key}\"排序后（{order_desc}）第{position}名：\n"
                f"数值：{rank_value}\n"
                f"完整记录：{rank_text}\n"
                f"以上结果已由系统精确计算，请直接引用。"
            )
            logger.info("排名结果: %s", result[:200])
            return result
        else:
            return f"总共只有 {len(records)} 条记录，没有第 {position} 名。"

    # ==================== 全量数据加载 ====================

    def _load_all_chunks(self, ctx) -> list:
        """惰性加载当前搜索文档的全部 chunk，供 sum/rank/read_all_rows 使用"""
        cached = self._ctx_all_chunks(ctx)
        if cached:
            return cached

        chunks = self._ctx_chunks(ctx)
        relevant_ids = list({doc.metadata.get("document_id")
                             for doc, _ in chunks or []
                             if doc.metadata.get("document_id")})
        if not relevant_ids:
            logger.warning("全量加载: 无相关文档 ID")
            return []

        full_filter = {"document_id": {"$in": relevant_ids}}
        AGG_EXCLUDE = ["总计", "合计", "小计"]
        all_chunks = self.vector_store.get_all_chunks(filter=full_filter)
        filtered = [
            (doc, 0.0) for doc, _ in all_chunks
            if doc.metadata.get("sheet_name") != "汇总"
            and not any(kw in doc.page_content for kw in AGG_EXCLUDE)
            and not self._is_empty_record(doc.page_content)
        ] or all_chunks
        self._ctx_set_all_chunks(ctx, filtered)
        logger.info("全量数据加载: %d 个文档, %d 个 chunk", len(relevant_ids), len(filtered))
        return filtered

    def _execute_read_all_rows(self, ctx) -> str:
        """返回已搜索文档的完整数据行"""
        if not self._ctx_chunks(ctx):
            logger.info("read_all_rows 被调用但无已搜索数据")
            return "没有可读取的数据，请先调用 search_documents 搜索相关内容。"

        chunks = self._load_all_chunks(ctx)
        if not chunks:
            logger.info("read_all_rows 被调用但未找到完整数据")
            return "未找到完整数据。"

        logger.info("read_all_rows 被调用，返回 %d 个 chunk", len(chunks))

        rows = []
        for doc, _ in chunks:
            source_name = doc.metadata.get("file_name", "未知文档")
            sheet = doc.metadata.get("sheet_name", "")
            label = f"{source_name} / {sheet}" if sheet else source_name
            rows.append(f"[{label}]\n{doc.page_content}")

        return "以下是完整数据：\n\n" + "\n\n".join(rows) + "\n\n以上为该文档全部数据。"


