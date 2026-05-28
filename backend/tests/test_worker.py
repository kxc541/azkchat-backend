import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch, call
from langchain_core.documents import Document

import worker


def make_chunks(n=3):
    return [
        Document(page_content=f"chunk {i}", metadata={"page_number": i})
        for i in range(1, n + 1)
    ]


def make_docs(n=2):
    return [Document(page_content=f"page {i}", metadata={}) for i in range(1, n + 1)]


class TestProcessFile:
    def _run(self, tmp_path, filename="test.txt", uid="user1", doc_id="doc1",
             docs=None, chunks=None, image_paths=None):
        docs = docs or make_docs()
        chunks = chunks or make_chunks()
        image_paths = image_paths or []

        with patch("worker.load_file", return_value=docs), \
             patch("worker.chunk_documents", return_value=chunks), \
             patch("worker.extract_pdf_images", return_value=image_paths), \
             patch("worker.store_chunk"), \
             patch("worker._update_state") as mock_state:
            worker.process_file(tmp_path, filename, uid, doc_id)

        return mock_state

    def test_marks_status_ready_on_success(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        try:
            mock_state = self._run(path)
            final_call_kwargs = mock_state.call_args_list[-1][1]
            assert final_call_kwargs.get("status") == "ready"
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_deletes_temp_file_on_success(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        self._run(path)
        assert not os.path.exists(path)

    def test_deletes_temp_file_on_failure(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        with patch("worker.load_file", side_effect=Exception("load failed")), \
             patch("worker._update_state"):
            worker.process_file(path, "test.txt", "user1", "doc1")

        assert not os.path.exists(path)

    def test_marks_status_failed_on_exception(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        with patch("worker.load_file", side_effect=RuntimeError("bad file")), \
             patch("worker._update_state") as mock_state:
            worker.process_file(path, "test.txt", "user1", "doc1")

        failed_calls = [
            c for c in mock_state.call_args_list
            if c[1].get("status") == "failed"
        ]
        assert len(failed_calls) == 1

    def test_raises_when_no_documents_extracted(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        with patch("worker.load_file", return_value=[]), \
             patch("worker._update_state") as mock_state:
            worker.process_file(path, "test.txt", "user1", "doc1")

        failed_calls = [
            c for c in mock_state.call_args_list
            if c[1].get("status") == "failed"
        ]
        assert len(failed_calls) == 1

    def test_rejects_pdf_exceeding_page_limit(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"content")
            path = f.name

        oversized_docs = make_docs(n=301)

        with patch("worker.load_file", return_value=oversized_docs), \
             patch("worker._update_state") as mock_state:
            worker.process_file(path, "big.pdf", "user1", "doc1")

        failed_calls = [
            c for c in mock_state.call_args_list
            if c[1].get("status") == "failed"
        ]
        assert len(failed_calls) == 1

    def test_rejects_too_many_chunks(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        oversized_chunks = make_chunks(n=8001)

        with patch("worker.load_file", return_value=make_docs()), \
             patch("worker.chunk_documents", return_value=oversized_chunks), \
             patch("worker._update_state") as mock_state:
            worker.process_file(path, "test.txt", "user1", "doc1")

        failed_calls = [
            c for c in mock_state.call_args_list
            if c[1].get("status") == "failed"
        ]
        assert len(failed_calls) == 1

    def test_image_extraction_failure_does_not_fail_job(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"content")
            path = f.name

        with patch("worker.load_file", return_value=make_docs()), \
             patch("worker.chunk_documents", return_value=make_chunks()), \
             patch("worker.extract_pdf_images", side_effect=Exception("poppler missing")), \
             patch("worker.store_chunk"), \
             patch("worker._update_state") as mock_state:
            worker.process_file(path, "test.pdf", "user1", "doc1")

        final_call_kwargs = mock_state.call_args_list[-1][1]
        assert final_call_kwargs.get("status") == "ready"

    def test_pdf_triggers_image_extraction(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as f:
            f.write(b"content")
            path = f.name

        with patch("worker.load_file", return_value=make_docs()), \
             patch("worker.chunk_documents", return_value=make_chunks()), \
             patch("worker.extract_pdf_images", return_value=[]) as mock_images, \
             patch("worker.store_chunk"), \
             patch("worker._update_state"):
            worker.process_file(path, "test.pdf", "user1", "doc1")

        mock_images.assert_called_once()

    def test_non_pdf_skips_image_extraction(self):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".txt") as f:
            f.write(b"content")
            path = f.name

        with patch("worker.load_file", return_value=make_docs()), \
             patch("worker.chunk_documents", return_value=make_chunks()), \
             patch("worker.extract_pdf_images") as mock_images, \
             patch("worker.store_chunk"), \
             patch("worker._update_state"):
            worker.process_file(path, "test.txt", "user1", "doc1")

        mock_images.assert_not_called()
