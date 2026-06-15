"""
Redis Stream 消费者：异步处理文档向量化任务

原理：
1. Java 后端上传文档后，将任务写入 Redis Stream (stream:ingest)
2. 本消费者从 Stream 中读取任务
3. 执行向量化（调用 MinIO 下载文件 → 解析 → 存入 ChromaDB）
4. 更新数据库状态
5. 确认消息已处理（XACK）

使用方式：
    python -m app.stream_consumer
"""

import json
import logging
import os
import time
import requests
import redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from app.config import settings

logger = logging.getLogger(__name__)

# Redis 连接（增加 socket_timeout 防止读取超时）
redis_client = redis.Redis(
    host="localhost",
    port=6379,
    password="sty01725",
    decode_responses=True,
    socket_timeout=10,
    socket_connect_timeout=5,
    retry_on_timeout=True
)

# Stream 配置
STREAM_KEY = "stream:ingest"
GROUP_NAME = "ingest-group"
CONSUMER_NAME = f"worker-{os.getpid()}"

# MinIO 配置（用于下载文件）
MINIO_ENDPOINT = "http://localhost:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_BUCKET = "rag-knowledge"

# Python AI 服务配置
PYTHON_BASE_URL = "http://localhost:8000"
INGEST_PATH = "/ingest/document"

# Java 后端配置（用于更新状态）
JAVA_BASE_URL = "http://localhost:8085"


def download_from_minio(file_path: str, local_path: str):
    """从 MinIO 下载文件到本地"""
    import minio
    client = minio.Minio(
        MINIO_ENDPOINT.replace("http://", ""),
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=False
    )
    client.fget_object(MINIO_BUCKET, file_path, local_path)


def update_document_status(document_id: int, status: str):
    """更新文档状态（通过 Java 后端 API）"""
    try:
        response = requests.put(
            f"{JAVA_BASE_URL}/document/{document_id}/status",
            json={"status": status},
            timeout=10
        )
        if response.status_code == 200:
            logger.info(f"文档状态更新成功: documentId={document_id}, status={status}")
        else:
            logger.error(f"文档状态更新失败: documentId={document_id}, status={response.status_code}")
    except Exception as e:
        logger.error(f"文档状态更新异常: documentId={document_id}, error={e}")


def process_message(message: dict):
    """处理单条向量化任务"""
    document_id = int(message["documentId"])
    file_path = message["filePath"]
    file_name = message["fileName"]

    logger.info(f"开始处理向量化任务: documentId={document_id}, fileName={file_name}")

    import tempfile
    import os

    temp_path = None
    try:
        # 1. 从 MinIO 下载文件
        suffix = os.path.splitext(file_name)[1] if "." in file_name else ""
        temp_path = os.path.join(tempfile.gettempdir(), f"rag_{document_id}_{int(time.time())}{suffix}")
        download_from_minio(file_path, temp_path)

        # 2. 调用 Python AI 服务进行向量化
        ingest_req = {
            "file_path": temp_path,
            "document_id": document_id,
            "file_name": file_name
        }
        response = requests.post(
            f"{PYTHON_BASE_URL}{INGEST_PATH}",
            json=ingest_req,
            timeout=120
        )

        if response.status_code == 200:
            update_document_status(document_id, "COMPLETED")
            logger.info(f"向量化成功: documentId={document_id}")
            return True
        else:
            update_document_status(document_id, "FAILED")
            logger.error(f"向量化失败: documentId={document_id}, status={response.status_code}")
            return False

    except Exception as e:
        update_document_status(document_id, "FAILED")
        logger.error(f"向量化异常: documentId={document_id}, error={e}")
        return False

    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def main():
    """主循环：持续消费 Stream 中的消息"""
    logger.info(f"Stream 消费者启动: consumer={CONSUMER_NAME}")

    while True:
        try:
            # 从 Stream 读取消息（阻塞 5 秒）
            messages = redis_client.xreadgroup(
                GROUP_NAME,
                CONSUMER_NAME,
                {STREAM_KEY: ">"},
                count=1,
                block=5000
            )

            if not messages:
                continue

            for stream_name, stream_messages in messages:
                for message_id, message_data in stream_messages:
                    logger.info(f"收到消息: id={message_id}, data={message_data}")

                    # 处理消息
                    success = process_message(message_data)

                    # 确认消息已处理
                    if success:
                        redis_client.xack(STREAM_KEY, GROUP_NAME, message_id)
                        logger.info(f"消息已确认: id={message_id}")

        except RedisTimeoutError:
            # 超时是正常的（没有新消息时），不打印错误
            continue
        except Exception as e:
            logger.error(f"消费者异常: {e}")
            time.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
