import os
import csv
import tempfile
import pytest
from unittest.mock import MagicMock, patch
from langchain_core.documents import Document

import loader_utils


class TestLoadTxt:
    def test_reads_file_content(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("hello world")
            path = f.name

        try:
            docs = loader_utils.load_txt(path)
            assert len(docs) == 1
            assert docs[0].page_content == "hello world"
            assert docs[0].metadata["source"] == path
        finally:
            os.unlink(path)

    def test_returns_single_document(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write("line1\nline2\nline3")
            path = f.name

        try:
            docs = loader_utils.load_txt(path)
            assert len(docs) == 1
        finally:
            os.unlink(path)


class TestLoadCsv:
    def test_reads_csv_rows(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["name", "email"])
            writer.writerow(["Alice", "alice@example.com"])
            path = f.name

        try:
            docs = loader_utils.load_csv(path)
            assert len(docs) == 1
            assert "name, email" in docs[0].page_content
            assert "Alice, alice@example.com" in docs[0].page_content
        finally:
            os.unlink(path)


class TestLoadFile:
    def test_dispatches_pdf(self):
        with patch("loader_utils.load_pdf", return_value=["doc"]) as mock:
            result = loader_utils.load_file("file.pdf", user_id="u1")
        mock.assert_called_once_with("file.pdf", user_id="u1")
        assert result == ["doc"]

    def test_dispatches_txt(self):
        with patch("loader_utils.load_txt", return_value=["doc"]) as mock:
            result = loader_utils.load_file("file.txt")
        mock.assert_called_once_with("file.txt")
        assert result == ["doc"]

    def test_dispatches_csv(self):
        with patch("loader_utils.load_csv", return_value=["doc"]) as mock:
            result = loader_utils.load_file("file.csv")
        mock.assert_called_once_with("file.csv")

    def test_dispatches_docx(self):
        with patch("loader_utils.load_docx", return_value=["doc"]) as mock:
            result = loader_utils.load_file("file.docx")
        mock.assert_called_once_with("file.docx")

    def test_raises_for_unsupported_extension(self):
        with pytest.raises(ValueError, match="Unsupported file format"):
            loader_utils.load_file("file.xlsx")


class TestChunkDocuments:
    def _make_doc(self, content, page_number=None):
        metadata = {}
        if page_number is not None:
            metadata["page_number"] = page_number
        return Document(page_content=content, metadata=metadata)

    def test_chunks_long_document(self):
        doc = self._make_doc("word " * 500, page_number=1)
        chunks = loader_utils.chunk_documents([doc])
        assert len(chunks) > 1

    def test_preserves_page_number_metadata(self):
        doc = self._make_doc("word " * 500, page_number=3)
        chunks = loader_utils.chunk_documents([doc])
        for chunk in chunks:
            assert chunk.metadata["page_number"] == 3

    def test_assigns_sequential_page_number_when_missing(self):
        # A single long doc without page_number gets chunks numbered 1, 2, 3...
        doc = self._make_doc("word " * 500)
        chunks = loader_utils.chunk_documents([doc])
        page_numbers = [c.metadata["page_number"] for c in chunks]
        assert page_numbers == list(range(1, len(chunks) + 1))

    def test_returns_documents_with_page_content(self):
        doc = self._make_doc("hello world", page_number=1)
        chunks = loader_utils.chunk_documents([doc])
        assert all(isinstance(c, Document) for c in chunks)
        assert all(len(c.page_content) > 0 for c in chunks)
