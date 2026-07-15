"""提示词管理器：从 prompts.yaml 加载所有提示词模板"""

import yaml
from pathlib import Path


class PromptManager:
    """懒加载 YAML 提示词文件，按 key 路径访问"""

    _prompts: dict | None = None
    _path: Path = Path(__file__).parent / "prompts.yaml"

    @classmethod
    def initialize(cls, path: str | Path | None = None):
        if path:
            cls._path = Path(path)
        with open(cls._path, encoding="utf-8") as f:
            cls._prompts = yaml.safe_load(f)

    @classmethod
    def get(cls, *keys: str) -> str:
        """按 key 链获取提示词，如 PromptManager.get('rag', 'template')"""
        if cls._prompts is None:
            cls.initialize()
        val = cls._prompts
        for key in keys:
            val = val[key]
        return val
