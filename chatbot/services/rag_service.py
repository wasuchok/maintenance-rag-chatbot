import os
import uuid
from typing import Dict, List, Optional

import chromadb
import requests
from django.conf import settings

from ..models import KnowledgeDocument
from .knowledge_access_service import get_accessible_knowledge_document_ids

CHROMA_DIR = os.path.join(settings.BASE_DIR, "chroma_data")
client = chromadb.PersistentClient(path=CHROMA_DIR)

collection = client.get_or_create_collection(name="knowledge_base")

OLLAMA_EMBED_URL = settings.OLLAMA_EMBED_URL
EMBED_MODEL = settings.OLLAMA_EMBED_MODEL

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
            "source" : document.source or "",
            "visibility" : document.visibility,
            "owner_id" : document.owner_id if document.owner_id is not None else -1,
            "owner_username" : (
                document.owner.get_username() if document.owner_id is not None else ""
            ),
        })

        embeddings.append(embed_text(chunk))

    if ids:
        collection.add(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
            embeddings=embeddings
        )

def normalize_text(text : str) -> str:
    return " ".join((text or "").strip().lower().split())

def deduplicate_results(items : List[Dict]) -> List[Dict]:
    seen = set()
    unique_items = []

    for item in items:
        metadata = item.get("metadata", {}) or {}

        key = (
            metadata.get("document_id"),
            metadata.get("chunk_index"),
            normalize_text(item.get("content", ""))
        )

        if key in seen:
            continue

        seen.add(key)
        unique_items.append(item)

    return unique_items

def search_knowledge(
    query: str,
    top_k: int = 5,
    max_distance: float = 1.2,
    user_id: Optional[int] = None,
    can_manage_all: bool = False,
) -> List[Dict]:
    accessible_document_ids = get_accessible_knowledge_document_ids(
        user_id=user_id,
        can_manage_all=can_manage_all,
    )

    if not accessible_document_ids:
        return []

    query_embedding =  embed_text(query)

    result =  collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where={"document_id": {"$in": accessible_document_ids}},
        include=["documents", "metadatas", "distances"]
    )

    documents = result.get("documents", [[]])[0]
    metadatas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]

    items = []
    for i in range(len(documents)):
        distance = distances[i] if i < len(distances) else None

        if distance is not None and distance > max_distance:
            continue

        items.append({
            "content" : documents[i],
            "metadata" : metadatas[i] or {},
            "distance" : distance
        })

    items = deduplicate_results(items)
    items.sort(key=lambda x: x["distance"] if x["distance"] is not None else 9999)
    return items

def delete_document_from_index(document_id : int) :
    collection.delete(
        where={"document_id" : document_id}
    )
