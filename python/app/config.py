from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # LLM
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model_name: str = "deepseek-chat"

    # Embedding
    embedding_model_name: str = "nomic-embed-text"
    embedding_device: str = "cpu"

    # Ollama
    ollama_base_url: str = "http://localhost:11434"

    # Vector store
    vector_store_path: str = "./chroma_db"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
