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
import tempfile
import time

import minio
import pika
import requests

from app.config import settings

logger = logging.getLogger(__name__)

_minio_client: minio.Minio | None = None


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


def process_message(ch, method, properties, body):
    """处理单条向量化任务（RabbitMQ 回调）"""
    try:
        message = json.loads(body)
    except Exception as e:
        logger.error(f"消息解析失败: {e}, body={body}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return

    document_id = int(message["documentId"])
    file_path = message["filePath"]
    file_name = message["fileName"]

    logger.info(f"开始处理向量化任务: documentId={document_id}, fileName={file_name}")

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
            f"{settings.python_base_url}{settings.ingest_path}",
            json=ingest_req,
            timeout=120
        )

        if response.status_code == 200:
            update_document_status(document_id, "COMPLETED")
            logger.info(f"向量化成功: documentId={document_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            update_document_status(document_id, "FAILED")
            logger.error(f"向量化失败: documentId={document_id}, status={response.status_code}")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

    except Exception as e:
        update_document_status(document_id, "FAILED")
        logger.error(f"向量化异常: documentId={document_id}, error={e}")
        try:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception:
            pass

    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


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
