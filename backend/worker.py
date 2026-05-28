import os
from datetime import datetime

from loader_utils import load_file, chunk_documents, extract_pdf_images
from weaviate_utils import store_chunk
from firebase_admin_init import db
from logger import get_logger

log = get_logger(__name__)


def _update_state(uid, doc_id, **fields):
    now = datetime.utcnow().isoformat() + "Z"

    fields["last_update"] = now
    fields["last_progress_at"] = now

    db.collection("users").document(uid).collection("files").document(doc_id).set(
        fields,
        merge=True,
    )


def process_file(temp_file_path: str, filename: str, uid: str, doc_id: str):

    MAX_PAGES = 300
    MAX_CHUNKS = 8000
    MAX_IMAGES = 300
    MAX_TOTAL_TEXT_CHARS = 10_000_000  # ~10MB text

    try:
        log.info("worker_started", uid=uid, doc_id=doc_id, filename=filename)
        now = datetime.utcnow().isoformat() + "Z"

        _update_state(
            uid,
            doc_id,
            status="processing",
            stage="loading",
            filename=filename,
            processing_started_at=now,
            last_update=now,
            last_progress_at=now,
            started_at=now,
        )

        documents = load_file(temp_file_path, user_id=uid)

        if not documents:
            raise RuntimeError("No documents extracted")

        if len(documents) > MAX_PAGES:
            raise RuntimeError(f"PDF exceeds page limit ({MAX_PAGES})")

        _update_state(
            uid,
            doc_id,
            stage="chunking",
            total_text_pages=len(documents),
            total_pages=len(documents),
        )

        chunks = chunk_documents(documents)

        if len(chunks) > MAX_CHUNKS:
            raise RuntimeError(f"Too many chunks generated ({len(chunks)} > {MAX_CHUNKS})")

        total_chars = 0
        for c in chunks:
            total_chars += len(getattr(c, "page_content", ""))
            if total_chars > MAX_TOTAL_TEXT_CHARS:
                raise RuntimeError("Total extracted text exceeds safe limit")

        _update_state(
            uid,
            doc_id,
            stage="embedding",
            total_chunks=len(chunks),
        )

        image_paths = []
        page_to_image = {}

        if temp_file_path.lower().endswith(".pdf"):
            _update_state(
                uid,
                doc_id,
                stage="images",
                image_pages_done=0,
                pages_done=0,
            )

            def on_image_page_start(page_num, total_pages):
                _update_state(
                    uid,
                    doc_id,
                    current_image_page=page_num,
                    total_image_pages=total_pages,
                )

            def on_image_page_done(page_num, total_pages):
                _update_state(
                    uid,
                    doc_id,
                    image_pages_done=page_num,
                    total_image_pages=total_pages,
                    pages_done=page_num,
                    total_pages=total_pages,
                )

            try:
                image_paths = extract_pdf_images(
                    temp_file_path,
                    user_id=uid,
                    upload_id=doc_id,
                    on_page_start=on_image_page_start,
                    on_page_done=on_image_page_done,
                )
            except Exception as e:
                log.warning("worker_image_extraction_failed", uid=uid, doc_id=doc_id, error=str(e))
                image_paths = []

            if len(image_paths) > MAX_IMAGES:
                raise RuntimeError(
                    f"Too many images extracted ({len(image_paths)} > {MAX_IMAGES})"
                )

            for idx, path in enumerate(image_paths, start=1):
                rel_path = "/" + path.replace("\\", "/").lstrip("/")
                page_to_image[idx] = rel_path

        _update_state(uid, doc_id, stage="storing", chunks_done=0)

        for idx, chunk in enumerate(chunks, start=1):
            chunk_text = getattr(chunk, "page_content", str(chunk))
            page_number = chunk.metadata.get("page_number")
            image_url = page_to_image.get(page_number) if page_number else None

            store_chunk(
                chunk_text=chunk_text,
                filename=filename,
                page_number=page_number,
                uid=uid,
                image_url=image_url,
                upload_id=doc_id,
            )

            if idx % 100 == 0 or idx == len(chunks):
                _update_state(
                    uid,
                    doc_id,
                    chunks_done=idx,
                    total_chunks=len(chunks),
                )

        _update_state(
            uid,
            doc_id,
            status="ready",
            stage="complete",
            chunks=len(chunks),
            total_text_pages=len(documents),
            total_image_pages=len(image_paths),
            completed_at=datetime.utcnow().isoformat() + "Z",
        )

        log.info("worker_finished", uid=uid, doc_id=doc_id, filename=filename, chunks=len(chunks))

    except Exception as e:
        log.error("worker_failed", uid=uid, doc_id=doc_id, filename=filename, error=str(e), exc_info=True)

        _update_state(
            uid,
            doc_id,
            status="failed",
            stage="failed",
            error=str(e),
        )

    finally:
        if os.path.exists(temp_file_path):
            os.remove(temp_file_path)

