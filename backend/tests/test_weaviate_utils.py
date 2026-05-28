from unittest.mock import MagicMock, patch
import weaviate_utils


class TestCreateSchema:
    def test_creates_schema_when_class_missing(self):
        weaviate_utils.client.schema.get.return_value = {"classes": []}
        weaviate_utils.create_schema()
        weaviate_utils.client.schema.create.assert_called_once()

    def test_skips_creation_when_class_exists(self):
        weaviate_utils.client.schema.get.return_value = {
            "classes": [{"class": "DocumentChunk"}]
        }
        weaviate_utils.client.schema.create.reset_mock()
        weaviate_utils.create_schema()
        weaviate_utils.client.schema.create.assert_not_called()

    def test_does_not_raise_on_exception(self):
        weaviate_utils.client.schema.get.side_effect = Exception("connection failed")
        weaviate_utils.create_schema()  # should not raise
        weaviate_utils.client.schema.get.side_effect = None


class TestStoreChunk:
    def setup_method(self):
        weaviate_utils.client.reset_mock()

    def test_stores_chunk_with_required_fields(self):
        with patch("weaviate_utils.generate_embedding", return_value=[0.1, 0.2]):
            weaviate_utils.store_chunk(
                chunk_text="some text",
                filename="doc.pdf",
                page_number=1,
                uid="user1",
            )

        call_kwargs = weaviate_utils.client.data_object.create.call_args
        data = call_kwargs[1]["data_object"] if call_kwargs[1] else call_kwargs[0][0]
        assert data["text"] == "some text"
        assert data["filename"] == "doc.pdf"
        assert data["ownerId"] == "user1"

    def test_includes_upload_id_when_provided(self):
        with patch("weaviate_utils.generate_embedding", return_value=[0.1]):
            weaviate_utils.store_chunk(
                chunk_text="text",
                filename="doc.pdf",
                page_number=1,
                uid="user1",
                upload_id="upload123",
            )

        call_kwargs = weaviate_utils.client.data_object.create.call_args
        data = call_kwargs[1]["data_object"] if call_kwargs[1] else call_kwargs[0][0]
        assert data["uploadId"] == "upload123"

    def test_includes_image_url_when_provided(self):
        with patch("weaviate_utils.generate_embedding", return_value=[0.1]):
            weaviate_utils.store_chunk(
                chunk_text="text",
                filename="doc.pdf",
                page_number=1,
                uid="user1",
                image_url="/images/page_1.png",
            )

        call_kwargs = weaviate_utils.client.data_object.create.call_args
        data = call_kwargs[1]["data_object"] if call_kwargs[1] else call_kwargs[0][0]
        assert data["image_url"] == "/images/page_1.png"

    def test_does_not_raise_on_exception(self):
        with patch("weaviate_utils.generate_embedding", side_effect=Exception("embed failed")):
            weaviate_utils.store_chunk(
                chunk_text="text",
                filename="doc.pdf",
                page_number=1,
                uid="user1",
            )  # should not raise


class TestDeleteChunks:
    def setup_method(self):
        weaviate_utils.client.reset_mock()

    def test_delete_file_chunks_calls_batch_delete(self):
        weaviate_utils.delete_file_chunks("upload123", "user1")
        weaviate_utils.client.batch.delete_objects.assert_called_once()

    def test_delete_all_chunks_calls_batch_delete(self):
        weaviate_utils.delete_all_chunks("user1")
        weaviate_utils.client.batch.delete_objects.assert_called_once()

    def test_delete_file_chunks_does_not_raise_on_exception(self):
        weaviate_utils.client.batch.delete_objects.side_effect = Exception("weaviate down")
        weaviate_utils.delete_file_chunks("upload123", "user1")  # should not raise
        weaviate_utils.client.batch.delete_objects.side_effect = None
