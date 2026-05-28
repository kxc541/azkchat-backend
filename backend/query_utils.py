import weaviate
import openai
import os
from embedding_utils import generate_embedding
from logger import get_logger

log = get_logger(__name__)

openai.api_key = os.getenv("OPENAI_API_KEY")
WEAVIATE_URL = os.getenv("WEAVIATE_URL", "http://weaviate:8080")
client = weaviate.Client(
    WEAVIATE_URL,
    startup_period=30
)


def query_answer(question, uid):
    log.info("rag_query", uid=uid, question=question)
    query_vector = generate_embedding(question)

    result = (
        client.query.get(
            "DocumentChunk",
            ["text", "filename", "page_number", "image_url", "_additional { certainty }"]
        )
        .with_near_vector({"vector": query_vector, "certainty": 0.2})
        .with_where({
            "path": ["ownerId"],
            "operator": "Equal",
            "valueString": uid,
        })
        .with_limit(15)
        .do()
    )

    docs = result.get("data", {}).get("Get", {}).get("DocumentChunk", [])
    if not docs:
        log.warning("rag_no_documents_found", uid=uid, question=question)
        return {"answer": "I don't know based on the document.", "images": []}

    seen = set()
    deduped = []
    for d in docs:
        key = (d.get("filename"), d.get("page_number"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(d)

    deduped.sort(key=lambda d: (d.get("filename", ""), d.get("page_number") or 0))
    context = "\n---\n".join(
        f"Filename: {d.get('filename', 'Unknown')}, "
        f"Page: {d.get('page_number', '?')}, "
        f"Certainty: {d.get('_additional', {}).get('certainty', 0):.2f}\n"
        f"{d.get('text', '')}"
        for d in deduped
    )
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are a helpful assistant. Answer the user's question using only the provided context."
            },
            {
                "role": "user",
                "content": f"Context:\n{context}\n\nQuestion: {question}"
            },
        ],
    )

    answer_text = response["choices"][0]["message"]["content"]
    q_lower = question.lower()
    factual = ["who is", "what is", "when is", "contact", "phone", "email", "address", "website"]
    if any(t in q_lower for t in factual):
        return {"answer": answer_text, "images": []}

    top_doc = max(deduped, key=lambda d: d.get("_additional", {}).get("certainty", 0))
    dominant_filename = top_doc.get("filename")
    dominant_top_page = top_doc.get("page_number") or 1
    allowed_pages = {dominant_top_page - 2, dominant_top_page - 1,
                     dominant_top_page,
                     dominant_top_page + 1, dominant_top_page + 2}

    images = []
    for d in deduped:
        if d.get("filename") != dominant_filename:
            continue

        page = d.get("page_number") or 0
        certainty = d.get("_additional", {}).get("certainty", 0)

        if page not in allowed_pages:
            continue

        if certainty < 0.20:
            continue

        img_url = d.get("image_url")
        if not img_url:
            continue

        img_url = img_url.replace("\\", "/")
        if not img_url.startswith("/"):
            img_url = "/" + img_url

        if img_url not in images:
            images.append(img_url)

    log.info("rag_answer_returned", uid=uid, images_count=len(images))
    return {
        "answer": answer_text,
        "images": images
    }
