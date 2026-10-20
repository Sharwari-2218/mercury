"""Docsense: upload a document and ask questions about it. (Databloom lives in databloom.py.)"""
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import ingest_department, require_type, scope_filter
from app.database import get_db
from app.models import User
from app.rag import answer_question, ingest_file
from app.rag.config import DEFAULT_COLLECTION, UPLOAD_DIR
from app.rag.ingest import load_chunks
from app.rag.search import matches

router = APIRouter(tags=["docsense"])

ALLOWED = {".pdf", ".docx", ".txt", ".md"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB, same limit as /contribute and the frontend
_READ_BLOCK = 1024 * 1024


def _save_upload(file: UploadFile, dest: Path) -> int:
    """Stream the upload to disk, stopping as soon as it passes the size limit."""
    size = 0
    with dest.open("wb") as out:
        while block := file.file.read(_READ_BLOCK):
            size += len(block)
            if size > MAX_FILE_SIZE:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(400, "File is too large (max 20 MB).")
            out.write(block)
    return size


# Plain `def`, not `async def`: saving and embedding are slow, blocking work.
# FastAPI runs plain-def routes in a worker thread, so other requests keep
# being served while a document is ingested.
@router.post("/docsense/upload")
def upload(
    file: UploadFile = File(...),
    department: str | None = Form(None),   # admins only: another department, or "all"
    user: User = Depends(require_type("doc")),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"Unsupported file type {ext or '(none)'}. Allowed: {sorted(ALLOWED)}")

    doc_id = uuid.uuid4().hex[:12]
    dest = UPLOAD_DIR / f"{doc_id}{ext}"
    if _save_upload(file, dest) == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, "The file is empty.")

    dept = ingest_department(user, department)
    try:
        result = ingest_file(
            dest,
            DEFAULT_COLLECTION,
            {
                "source": file.filename,
                "type": "doc",
                "department": dept,
                "uploaded_by": user.email,   # from the login, never the form
                "doc_id": doc_id,
            },
        )
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"Could not ingest file: {e}")

    if result["chunks"] == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "No readable text found in this file. Scanned PDFs need OCR first.")

    return {"doc_id": doc_id, "filename": file.filename, "department": dept, **result}


def visible_docs(user: User, db: Session) -> dict[str, dict]:
    """Documents this user may see, keyed by doc_id, in upload order."""
    where = scope_filter(user, db, type="doc")
    docs: dict[str, dict] = {}
    for c in load_chunks(DEFAULT_COLLECTION):
        m = c["metadata"]
        if not m.get("source") or not matches(m, where):
            continue
        key = m.get("doc_id") or m["source"]   # very old chunks may have no doc_id
        if key not in docs:
            docs[key] = {
                "doc_id": key,
                "file": m["source"],        # what the Ask page sends as `file`
                "name": m["source"],
                "filename": m["source"],    # what the Databloom page shows
                "department": m.get("department"),
                "uploaded_by": m.get("uploaded_by"),
            }
    return docs


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    doc_id: str | None = None      # restrict to one document by id
    file: str | None = None        # ...or by file name (what the Ask page sends)
    department: str | None = None
    detail: Literal["auto", "short", "detailed"] = "auto"


@router.post("/docsense/ask")
def ask(req: AskRequest, user: User = Depends(require_type("doc")), db: Session = Depends(get_db)):
    if req.doc_id or req.file:
        docs = visible_docs(user, db).values()
        found = any(d["doc_id"] == req.doc_id for d in docs) if req.doc_id \
            else any(d["file"] == req.file for d in docs)
        if not found:
            # Same answer for "doesn't exist" and "not yours", so documents can't be probed.
            raise HTTPException(404, "Document not found")

    where = scope_filter(
        user, db,
        type="doc",
        doc_id=req.doc_id,
        source=None if req.doc_id else req.file,
        department=req.department,
    )
    return answer_question(req.question, DEFAULT_COLLECTION, where, detail=req.detail)


@router.get("/docsense/files")
def files(user: User = Depends(require_type("doc")), db: Session = Depends(get_db)):
    """Documents this user may see, newest first, for the Ask and Databloom pickers."""
    return list(reversed(visible_docs(user, db).values()))
