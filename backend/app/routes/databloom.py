"""Databloom: list a document's chunks and summarize any one of them as markdown.

Reads from the same place every other feature writes to: the shared chunk store
(app.rag.ingest.load_chunks) for DEFAULT_COLLECTION in app.rag.config.

Endpoints (all need login + "doc" permission):
    GET  /databloom/files               file names (older picker)
    GET  /databloom/{doc_id}/chunks     one document's chunks, in order
    GET  /databloom/chunks?source=...   same, by file name (kept for older clients)
    POST /databloom                     { doc_id, chunk_id } -> { summary }
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.auth import can_see_chunk, require_type
from app.database import get_db
from app.models import User
from app.rag.config import DEFAULT_COLLECTION, get_llm
from app.rag.ingest import load_chunks

router = APIRouter(prefix="/databloom", tags=["databloom"])

PROMPT = """You summarize document excerpts for busy readers.
Use ONLY the text provided. Do not add outside facts.

Source file: {source}

Excerpt:
\"\"\"
{text}
\"\"\"

Write a markdown summary with:
## a short heading naming the topic
- 3 to 6 bullet points covering the main ideas
**Key point:** one sentence with the single most important takeaway.
If the excerpt is too short or meaningless to summarize, say so in one line."""

# So re-clicking "Summarize" doesn't burn API quota. Resets when the server restarts.
_summary_cache: dict[str, str] = {}


def _doc_key(meta: dict) -> str | None:
    return meta.get("doc_id") or meta.get("source")


def _visible_doc_chunks(user: User, db: Session) -> list[dict]:
    """Document chunks (not wikis) this user is allowed to see."""
    return [
        c for c in load_chunks(DEFAULT_COLLECTION)
        if c["metadata"].get("type") == "doc" and can_see_chunk(user, c["metadata"], db)
    ]


def _chunk_list(chunks: list[dict]) -> list[dict]:
    out = [
        {
            "chunk_id": c["id"],
            "index": c["metadata"].get("chunk_index", i),
            "page": c["metadata"].get("page"),
            "preview": c["text"][:200],
            "length": len(c["text"]),
            "summarized": c["id"] in _summary_cache,
        }
        for i, c in enumerate(chunks)
    ]
    out.sort(key=lambda c: c["index"])
    return out


@router.get("/files")
def list_files(user: User = Depends(require_type("doc")), db: Session = Depends(get_db)):
    """File names for the picker."""
    sources = {c["metadata"].get("source") for c in _visible_doc_chunks(user, db)}
    return {"files": sorted(s for s in sources if s)}


@router.get("/chunks")
def list_chunks_by_source(source: str, user: User = Depends(require_type("doc")),
                          db: Session = Depends(get_db)):
    """All chunks of one file (by file name), in document order."""
    chunks = [c for c in _visible_doc_chunks(user, db) if c["metadata"].get("source") == source]
    if not chunks:
        raise HTTPException(404, f"No chunks found for '{source}'")
    out = _chunk_list(chunks)
    return {"source": source, "filename": source, "count": len(out), "chunks": out}


@router.get("/{doc_id}/chunks")
def list_chunks(doc_id: str, user: User = Depends(require_type("doc")), db: Session = Depends(get_db)):
    """All chunks of one document (by doc_id), in document order. 404 if you can't see it."""
    chunks = [c for c in _visible_doc_chunks(user, db) if _doc_key(c["metadata"]) == doc_id]
    if not chunks:
        raise HTTPException(404, "Document not found")
    source = chunks[0]["metadata"].get("source")
    out = _chunk_list(chunks)
    return {"doc_id": doc_id, "source": source, "filename": source, "count": len(out), "chunks": out}


class SummarizeRequest(BaseModel):
    chunk_id: str
    doc_id: str | None = None   # what the Databloom page sends
    source: str | None = None   # older clients send the file name instead

    @model_validator(mode="after")
    def _needs_a_document(self):
        if not self.doc_id and not self.source:
            raise ValueError("Send doc_id (or source) with the chunk_id")
        return self


@router.post("")
def summarize_chunk(req: SummarizeRequest, user: User = Depends(require_type("doc")),
                    db: Session = Depends(get_db)):
    """Fetch one chunk by ID and return a markdown summary of it."""
    chunk = next((c for c in load_chunks(DEFAULT_COLLECTION) if c["id"] == req.chunk_id), None)

    # Permission check comes BEFORE the cache, so a cached summary can't leak to the wrong person.
    # 404 for everything (missing, not yours, wrong document), so chunk IDs can't be probed.
    if chunk is None or chunk["metadata"].get("type") != "doc" or not can_see_chunk(user, chunk["metadata"], db):
        raise HTTPException(404, "Chunk not found")
    meta = chunk["metadata"]
    if (req.doc_id and _doc_key(meta) != req.doc_id) or (not req.doc_id and meta.get("source") != req.source):
        raise HTTPException(404, "Chunk not found in this document")

    if req.chunk_id in _summary_cache:
        return {"chunk_id": req.chunk_id, "summary": _summary_cache[req.chunk_id], "cached": True}

    try:
        # get_llm() already retries free-tier rate limits with backoff.
        summary = get_llm(0.2).invoke(PROMPT.format(source=meta.get("source"), text=chunk["text"])).content
    except Exception:
        raise HTTPException(502, "The AI service is busy or unavailable right now. Please try again in a minute.")

    _summary_cache[req.chunk_id] = summary
    return {"chunk_id": req.chunk_id, "summary": summary, "cached": False}
