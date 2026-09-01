"""
RabbitMQ 消费者：异步处理文档向量化任务

原理：
1. Java 后端上传文档后，将任务写入 RabbitMQ (ingest.queue)
2. 本消费者从队列中读取任务
3. 执行向量化（调用 MinIO 下载文件 → 解析 → 存入 ChromaDB）
4. 更新数据库状态
5. 确认消息已处理（basic_ack）

使用方式：
    python -m app.stream_consumer
"""

import json
import logging
import os
import time

import minio
import pika
import requests

from app.config import settings

logger = logging.getLogger(__name__)

# 共享临时目录（与 python-ai 容器共享）
TEMP_DIR = "/tmp/rag_temp"

_minio_client: minio.Minio | None = None


def _internal_headers() -> dict[str, str]:
    return {"X-Internal-Service-Token": settings.resolved_internal_service_token}


def _get_minio_client() -> minio.Minio:
    """获取 MinIO 客户端（单例复用）"""
    global _minio_client
    if _minio_client is None:
        _minio_client = minio.Minio(
            settings.minio_endpoint.replace("http://", ""),
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
    return _minio_client


def download_from_minio(file_path: str, local_path: str):
    """从 MinIO 下载文件到本地"""
    _get_minio_client().fget_object(settings.minio_bucket, file_path, local_path)


def update_document_status(document_id: int, status: str):
    """更新文档状态（通过 Java 后端 API）"""
    try:
        response = requests.put(
            f"{settings.java_base_url}/document/{document_id}/status",
            json={"status": status},
            timeout=10
        )
        if response.status_code == 200:
            logger.info(f"文档状态更新成功: documentId={document_id}, status={status}")
        else:
            logger.error(f"文档状态更新失败: documentId={document_id}, status={response.status_code}")
    except Exception as e:
        logger.error(f"文档状态更新异常: documentId={document_id}, error={e}")


def mark_event_processing(event_id: str | None):
    if not event_id:
        return
    response = requests.put(
        f"{settings.java_base_url}/document/internal/index-events/{event_id}/processing",
        headers=_internal_headers(), timeout=10,
    )
    response.raise_for_status()


def mark_event_retry(event_id: str | None, error: Exception | str):
    if not event_id:
        return
    try:
        response = requests.put(
            f"{settings.java_base_url}/document/internal/index-events/{event_id}/retry",
            json={"error": str(error)[:900]}, headers=_internal_headers(), timeout=10,
        )
        response.raise_for_status()
    except Exception as callback_error:
        logger.error("索引事件失败状态回写异常: eventId=%s, error=%s", event_id, callback_error)


def activate_index_version(document_id: int, index_version: int, event_id: str | None):
    """Only expose a version after the Python ingestion request has completed."""
    response = requests.put(
        f"{settings.java_base_url}/document/{document_id}/index-ready",
        json={"indexVersion": index_version, "eventId": event_id or ""},
        headers=_internal_headers(), timeout=10,
    )
    response.raise_for_status()


def process_message(ch, method, properties, body):
    """处理单条向量化任务（RabbitMQ 回调）"""
    try:
        message = json.loads(body)
        # Compatibility for records published by the pre-v15 publisher, which
        # serialized the persisted JSON payload one extra time.
        if isinstance(message, str):
            message = json.loads(message)
        if not isinstance(message, dict):
            raise ValueError("消息体必须是 JSON 对象")
    except Exception as e:
        logger.error(f"消息解析失败: {e}, body={body}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    document_id = int(message["documentId"])
    index_version = int(message.get("indexVersion", 1))
    event_type = message.get("eventType", "UPSERT")
    event_id = message.get("eventId")
    file_path = message.get("filePath")
    file_name = message.get("fileName")

    logger.info("开始索引事件: eventId=%s, documentId=%d, version=%d, type=%s",
                message.get("eventId", "-"), document_id, index_version, event_type)

    temp_path = None
    try:
        mark_event_processing(event_id)
        if event_type == "DELETE":
            response = requests.delete(f"{settings.python_base_url}{settings.ingest_path}/{document_id}", timeout=120)
            response.raise_for_status()
            # DELETE has no document activation callback, but it is still a completed index event.
            activate_index_version(document_id, index_version, event_id)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            logger.info("索引删除完成: documentId=%d", document_id)
            return

        # 1. 从 MinIO 下载到共享临时目录
        os.makedirs(TEMP_DIR, exist_ok=True)
        suffix = os.path.splitext(file_name)[1] if "." in file_name else ""
        temp_path = os.path.join(TEMP_DIR, f"rag_{document_id}_{int(time.time())}{suffix}")
        download_from_minio(file_path, temp_path)

        # 2. 调用 Python AI 服务进行向量化（传递共享目录路径）
        ingest_req = {
            "file_path": temp_path,
            "document_id": document_id,
            "file_name": file_name,
            "index_version": index_version,
            "event_id": message.get("eventId"),
        }
        response = requests.post(
            f"{settings.python_base_url}{settings.ingest_path}",
            json=ingest_req,
            timeout=120
        )

        if response.status_code == 200:
            activate_index_version(document_id, index_version, event_id)
            logger.info("双索引构建并激活成功: documentId=%d, version=%d", document_id, index_version)
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            update_document_status(document_id, "FAILED")
            mark_event_retry(event_id, f"Python ingestion returned HTTP {response.status_code}")
            logger.error(f"向量化失败: documentId={document_id}, status={response.status_code}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    except Exception as e:
        update_document_status(document_id, "FAILED")
        mark_event_retry(event_id, e)
        logger.error(f"向量化异常: documentId={document_id}, error={e}")
        try:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            pass

    # 注意：临时文件由 ingestion.py 在向量化完成后清理


def main():
    """主函数：启动 RabbitMQ 消费者（带断线重连）"""
    logger.info("RabbitMQ 消费者启动")

    credentials = pika.PlainCredentials(
        settings.rabbitmq_user,
        settings.rabbitmq_password
    )

    while True:
        connection = None
        channel = None
        try:
            connection = pika.BlockingConnection(pika.ConnectionParameters(
                host=settings.rabbitmq_host,
                port=settings.rabbitmq_port,
                virtual_host=settings.rabbitmq_vhost,
                credentials=credentials,
                heartbeat=600,
                blocked_connection_timeout=300,
            ))
            channel = connection.channel()

            # 声明交换机和队列（确保存在）
            channel.exchange_declare(
                exchange=settings.rabbitmq_ingest_exchange,
                exchange_type="direct",
                durable=True,
            )
            channel.queue_declare(
                queue=settings.rabbitmq_ingest_queue,
                durable=True,
            )
            channel.queue_bind(
                queue=settings.rabbitmq_ingest_queue,
                exchange=settings.rabbitmq_ingest_exchange,
                routing_key=settings.rabbitmq_ingest_routing_key,
            )

            # 每次只取一条消息，处理完再取下一条
            channel.basic_qos(prefetch_count=1)

            # 开始消费
            channel.basic_consume(
                queue=settings.rabbitmq_ingest_queue,
                on_message_callback=process_message,
                auto_ack=False,
            )

            logger.info("等待 RabbitMQ 消息中...")
            channel.start_consuming()

        except KeyboardInterrupt:
            logger.info("消费者收到中断信号，正在退出...")
            if channel:
                channel.stop_consuming()
            if connection:
                connection.close()
            logger.info("RabbitMQ 连接已关闭")
            break
        except pika.exceptions.AMQPError as e:
            logger.warning(f"RabbitMQ 连接异常，5秒后重连: {e}")
            time.sleep(5)
        except Exception as e:
            logger.error(f"消费者异常，5秒后重连: {e}")
            time.sleep(5)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
