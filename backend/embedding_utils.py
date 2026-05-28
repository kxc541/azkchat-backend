import openai
import os
from dotenv import load_dotenv
from logger import get_logger

load_dotenv()

log = get_logger(__name__)
openai.api_key = os.getenv("OPENAI_API_KEY")

def generate_embedding(text: str):
    try:
        response = openai.Embedding.create(
            model="text-embedding-ada-002",
            input=text,
        )
        return response["data"][0]["embedding"]
    except Exception as e:
        log.error("embedding_failed", error=str(e), exc_info=True)
        raise
