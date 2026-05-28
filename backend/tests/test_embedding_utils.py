import pytest
from unittest.mock import patch, MagicMock
import embedding_utils


class TestGenerateEmbedding:
    def test_returns_vector_on_success(self):
        fake_vector = [0.1, 0.2, 0.3]
        mock_response = {"data": [{"embedding": fake_vector}]}

        with patch("embedding_utils.openai.Embedding.create", return_value=mock_response):
            result = embedding_utils.generate_embedding("hello world")

        assert result == fake_vector

    def test_raises_on_openai_failure(self):
        with patch("embedding_utils.openai.Embedding.create", side_effect=Exception("API error")):
            with pytest.raises(Exception, match="API error"):
                embedding_utils.generate_embedding("hello world")

    def test_passes_correct_model_and_input(self):
        mock_response = {"data": [{"embedding": [0.1]}]}

        with patch("embedding_utils.openai.Embedding.create", return_value=mock_response) as mock_create:
            embedding_utils.generate_embedding("test input")

        mock_create.assert_called_once_with(
            model="text-embedding-ada-002",
            input="test input",
        )
