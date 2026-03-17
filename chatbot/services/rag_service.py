import os
import uuid
import requests
import chromadb

from typing import List
from django.conf import settings
from ..models import KnowledgeDocument

CHROMA_DIR = os.path.join(settings.BASE_DIR, "chroma_data")
client = chromadb.PersistentClient(path=CHROMA_DIR)

collection = client.get_or_create_collection(name="knowledge_base")

OLLAMA_EMBED_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "embeddinggemma"

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> List[str]:
    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap

    return chunks

def embed_text(text: str) -> List[float]:
    response = requests.post(
        OLLAMA_EMBED_URL,
        json={
            "model": EMBED_MODEL,
            "input" : text
        },
        timeout=120
    )

    response.raise_for_status()
    data = response.json()

    return data["embeddings"][0]

def index_document(document: KnowledgeDocument):
    chunks = chunk_text(document.content)

    ids = []
    documents = []
    metadatas = []
    embeddings = []

    for i, chunk in enumerate(chunks):
        ids.append(str(uuid.uuid4()))
        documents.append(chunk)
        metadatas.append({
            "document_id" : document.id,
            "title" : document.title,
            "chunk_index" : i,
            "source" : document.source or ""
        })

        embeddings.append(embed_text(chunk))

    if ids:
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings
        )

def search_knowledge(query: str, top_k : int =  3) -> List[str]:
    query_embedding =  embed_text(query)

    result =  collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k
    )

    docs =  result.get("documents", [[]])[0]
    return docs