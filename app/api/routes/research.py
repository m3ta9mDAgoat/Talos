from __future__ import annotations

from io import BytesIO
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.research import ResearchArtifactRecord
from app.services.research_service import draft_strategy

router = APIRouter(prefix="/research", tags=["research"])


def _row(r: ResearchArtifactRecord) -> dict:
    meta = dict(r.metadata_json or {})
    return {
        "id": r.id, "kind": r.kind, "title": r.title, "url": r.url, "doi": r.doi,
        "abstract": r.abstract, "created_at": r.created_at,
        "pages": meta.get("pages"), "text_chars": meta.get("text_chars"),
        "filename": meta.get("filename"), "has_text": bool(r.notes),
        "strategy_spec": meta.get("strategy_spec"),
    }


@router.get("")
def list_papers(db: Session = Depends(get_db)):
    rows = db.query(ResearchArtifactRecord).filter(ResearchArtifactRecord.kind == "paper_upload").order_by(ResearchArtifactRecord.id.desc()).all()
    return [_row(x) for x in rows]


@router.post("/upload")
async def upload_paper(file: UploadFile = File(...), db: Session = Depends(get_db)):
    name = file.filename or "paper.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, "Upload a PDF research paper")
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(400, "PDF is larger than 25 MB")
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(raw))
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        pages = len(reader.pages)
    except Exception as exc:
        raise HTTPException(400, f"Could not parse PDF: {exc}") from exc
    cleaned = text.strip()
    if not cleaned:
        raise HTTPException(400, "No extractable text found in this PDF")
    title = name.rsplit(".", 1)[0]
    row = ResearchArtifactRecord(
        kind="paper_upload", title=title, notes=cleaned[:250_000],
        abstract=cleaned[:3000], metadata_json={"filename": name, "pages": pages, "text_chars": len(cleaned)},
    )
    db.add(row); db.commit(); db.refresh(row)
    return _row(row)


@router.post("/{paper_id}/extract-strategy")
async def extract_strategy(paper_id: int, db: Session = Depends(get_db)):
    row = db.get(ResearchArtifactRecord, paper_id)
    if not row or row.kind != "paper_upload":
        raise HTTPException(404, "Research paper not found")
    if not row.notes:
        raise HTTPException(400, "Paper has no extracted text")
    spec = await draft_strategy(row.notes, name=row.title)
    meta = dict(row.metadata_json or {}); meta["strategy_spec"] = spec; row.metadata_json = meta
    db.commit()
    return {"paper_id": row.id, "title": row.title, "strategy_spec": spec}
