"""Tool schemas for RAG engine tool calling (OpenAI-format function definitions)"""

SEARCH_DOCUMENTS_TOOL = {
    "type": "function",
    "function": {
        "name": "search_documents",
        "description": "从知识库中搜索与问题相关的文档内容。需要查找具体信息、数据、记录时调用。query参数应是一个完整、具体的搜索关键词，可以结合对话历史对问题进行改写，补充学号、实验名称等关键信息。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询语句，应包含关键术语、学号、实验名称等，尽可能完整具体"
                }
            },
            "required": ["query"],
        },
    },
}

CALCULATE_SUM_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_sum",
        "description": (
            '对已检索到的文档内容中指定列（key）的数值进行精确求和。'
            '当用户问“总共”、“合计”、“一共多少钱”、“总共有多少”等需要加总的问题时调用。'
            '必须先调用 search_documents 获取数据后才能使用此工具。'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key_name": {
                    "type": "string",
                    "description": (
                        '要加总的列名（key），如“结果”、“金额”、'
                        '“价格”、“得分”等。'
                        '应该从搜索到的数据中存在的数值列中选择。'
                    ),
                },
                "row_filter": {
                    "type": "string",
                    "description": (
                        '行过滤条件。格式为“前N行”、“第N行之后”、'
                        '“第N行之前”。没有过滤条件则传空字符串。'
                    ),
                },
            },
            "required": ["key_name"],
        },
    },
}

CALCULATE_RANK_TOOL = {
    "type": "function",
    "function": {
        "name": "calculate_rank",
        "description": (
            '从已检索到的文档内容中，对指定列（key）的数值进行排序，'
            '返回指定排名位置的记录。当用户问“最贵”、“最便宜”、'
            '“第三高”、“前五名”等排名问题时调用。'
            '必须先调用 search_documents 获取数据后才能使用此工具。'
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "key_name": {
                    "type": "string",
                    "description": (
                        '排序列名（key），如“结果”、“金额”、'
                        '“价格”、“得分”等。'
                        '应该从搜索到的数据中存在的数值列中选择。'
                    ),
                },
                "ascending": {
                    "type": "boolean",
                    "description": (
                        '排序方向。true=升序（从低到高，用于“最便宜/最低/最小/最少”），'
                        'false=降序（从高到低，用于“最贵/最高/最大/最多”）'
                    ),
                },
                "position": {
                    "type": "integer",
                    "description": "返回第几名。1表示第一名（最值），2表示第二名，以此类推。默认1。"
                },
            },
            "required": ["key_name", "ascending", "position"],
        },
    },
}

TOOLS = [SEARCH_DOCUMENTS_TOOL, CALCULATE_SUM_TOOL, CALCULATE_RANK_TOOL]
