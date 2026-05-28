import weaviate
import os
import uuid
from dotenv import load_dotenv
from embedding_utils import generate_embedding
from logger import get_logger

load_dotenv()

log = get_logger(__name__)

WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
client = weaviate.Client(
    WEAVIATE_URL,
    startup_period=30
)


def create_schema():
    schema = {
        "classes": [
            {
                "class": "DocumentChunk",
                "description": "A chunk of a document belonging to a specific user and upload.",
                "properties": [
                    {"name": "text", "dataType": ["text"], "tokenization": "word"},
                    {"name": "filename", "dataType": ["text"], "tokenization": "whitespace"},
                    {"name": "page_number", "dataType": ["int"]},
                    {"name": "ownerId", "dataType": ["text"], "tokenization": "whitespace"},
                    {"name": "uploadId", "dataType": ["text"], "tokenization": "whitespace"},
                    {"name": "image_url", "dataType": ["text"], "tokenization": "whitespace"},
                ],
                "vectorizer": "none",
            }
        ]
    }

    try:
        existing = client.schema.get()
        if not any(cls["class"] == "DocumentChunk" for cls in existing.get("classes", [])):
            client.schema.create(schema)
            log.info("weaviate_schema_created")
        else:
            log.info("weaviate_schema_exists")
    except Exception as e:
        log.error("weaviate_schema_failed", error=str(e), exc_info=True)


def store_chunk(chunk_text, filename, page_number, uid, image_url=None, upload_id=None):
    try:
        embedding = generate_embedding(chunk_text)
        object_id = str(uuid.uuid4())

        data = {
            "text": chunk_text,
            "filename": filename,
            "page_number": page_number,
            "ownerId": uid,
        }

        if upload_id:
            data["uploadId"] = upload_id

        if image_url:
            data["image_url"] = image_url

        client.data_object.create(
            data_object=data,
            class_name="DocumentChunk",
            vector=embedding,
            uuid=object_id,
        )

    except Exception as e:
        log.error("weaviate_store_chunk_failed", filename=filename, page=page_number, error=str(e), exc_info=True)


def delete_file_chunks(upload_id: str, uid: str):
    try:
        where_filter = {
            "operator": "And",
            "operands": [
                {"path": ["uploadId"], "operator": "Equal", "valueText": upload_id},
                {"path": ["ownerId"], "operator": "Equal", "valueText": uid},
            ],
        }

        client.batch.delete_objects(
            class_name="DocumentChunk",
            where=where_filter,
            output="verbose",
        )
        log.info("weaviate_chunks_deleted", upload_id=upload_id, uid=uid)

    except Exception as e:
        log.error("weaviate_delete_chunks_failed", upload_id=upload_id, uid=uid, error=str(e), exc_info=True)


def delete_all_chunks(uid: str):
    try:
        where_filter = {
            "path": ["ownerId"],
            "operator": "Equal",
            "valueText": uid,
        }
        client.batch.delete_objects(
            class_name="DocumentChunk",
            where=where_filter,
            output="verbose",
        )
        log.info("weaviate_all_chunks_deleted", uid=uid)
    except Exception as e:
        log.error("weaviate_delete_all_chunks_failed", uid=uid, error=str(e), exc_info=True)
