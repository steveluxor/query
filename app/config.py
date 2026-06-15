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

    # RabbitMQ
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_user: str = "guest"
    rabbitmq_password: str = "guest"
    rabbitmq_vhost: str = "/"
    rabbitmq_ingest_exchange: str = "ingest.exchange"
    rabbitmq_ingest_queue: str = "ingest.queue"
    rabbitmq_ingest_routing_key: str = "ingest.routing"

    # MinIO
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "rag-knowledge"

    # Python AI 服务（自引用，用于内部调用）
    python_base_url: str = "http://localhost:8000"
    ingest_path: str = "/ingest/document"

    # Java 后端
    java_base_url: str = "http://localhost:8085"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
