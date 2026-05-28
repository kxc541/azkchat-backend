from unittest.mock import patch, MagicMock
import query_utils


def make_doc(filename, page_number, certainty, image_url=None, text="some content"):
    return {
        "text": text,
        "filename": filename,
        "page_number": page_number,
        "image_url": image_url,
        "_additional": {"certainty": certainty},
    }


def stub_weaviate(docs):
    (
        query_utils.client.query
        .get.return_value
        .with_near_vector.return_value
        .with_where.return_value
        .with_limit.return_value
        .do.return_value
    ) = {"data": {"Get": {"DocumentChunk": docs}}}


def stub_openai(answer="The answer is here."):
    return patch(
        "query_utils.openai.ChatCompletion.create",
        return_value={"choices": [{"message": {"content": answer}}]},
    )


class TestQueryAnswer:
    def setup_method(self):
        query_utils.client.reset_mock()

    def test_returns_default_when_no_docs_found(self):
        stub_weaviate([])
        with patch("query_utils.generate_embedding", return_value=[0.1]):
            result = query_utils.query_answer("what is the policy?", "user1")

        assert result["answer"] == "I don't know based on the document."
        assert result["images"] == []

    def test_returns_answer_when_docs_found(self):
        docs = [make_doc("doc.pdf", 1, certainty=0.9, image_url="/images/page_1.png")]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai("Here is your answer."):
            result = query_utils.query_answer("what is the refund policy?", "user1")

        assert result["answer"] == "Here is your answer."

    def test_factual_question_returns_no_images(self):
        docs = [make_doc("doc.pdf", 1, certainty=0.9, image_url="/images/page_1.png")]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai("John Smith"):
            result = query_utils.query_answer("who is the contact?", "user1")

        assert result["images"] == []

    def test_deduplicates_docs_by_filename_and_page(self):
        docs = [
            make_doc("doc.pdf", 1, certainty=0.9),
            make_doc("doc.pdf", 1, certainty=0.85),  # duplicate
        ]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai():
            result = query_utils.query_answer("tell me about returns", "user1")

        assert result["answer"] is not None

    def test_excludes_image_below_certainty_threshold(self):
        docs = [make_doc("doc.pdf", 1, certainty=0.15, image_url="/images/page_1.png")]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai():
            result = query_utils.query_answer("tell me about the product", "user1")

        assert result["images"] == []

    def test_excludes_image_outside_page_window(self):
        docs = [
            make_doc("doc.pdf", 1, certainty=0.95, image_url="/images/page_1.png"),
            make_doc("doc.pdf", 10, certainty=0.90, image_url="/images/page_10.png"),
        ]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai():
            result = query_utils.query_answer("tell me about the product", "user1")

        assert not any("page_10" in img for img in result["images"])

    def test_excludes_image_from_different_filename(self):
        docs = [
            make_doc("doc_a.pdf", 1, certainty=0.95, image_url="/images/a_page_1.png"),
            make_doc("doc_b.pdf", 1, certainty=0.90, image_url="/images/b_page_1.png"),
        ]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai():
            result = query_utils.query_answer("describe the document", "user1")

        assert len(result["images"]) == 1

    def test_normalizes_image_url_backslashes(self):
        docs = [make_doc("doc.pdf", 1, certainty=0.9, image_url="images\\page_1.png")]
        stub_weaviate(docs)

        with patch("query_utils.generate_embedding", return_value=[0.1]), stub_openai():
            result = query_utils.query_answer("describe the document", "user1")

        if result["images"]:
            assert "\\" not in result["images"][0]
            assert result["images"][0].startswith("/")
