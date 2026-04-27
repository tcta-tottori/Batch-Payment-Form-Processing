"""FastAPI HTTP server (section 5 of the spec)."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from .classifier import classify_pdf
from .formatters import to_full_tsv, to_markdown, to_tsv_files
from .models import MonthlyChecklist
from .orchestrator import build_sorted_permit_pdf, process_classified_documents


SESSION_TTL_SECONDS = 24 * 60 * 60  # 24h per spec section 8.2


class SessionStore:
    """In-memory session store with on-disk PDF mirrors.

    Each session has a temp directory holding the original PDFs (so we can
    re-extract permits) and the latest checklist JSON.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root or tempfile.mkdtemp(prefix="bpfp-"))
        self._sessions: Dict[str, dict] = {}
        self._lock = threading.Lock()

    def create(self, files: List[tuple]) -> str:
        sid = str(uuid.uuid4())
        sess_dir = self.root / sid
        sess_dir.mkdir(parents=True, exist_ok=True)
        saved_paths = []
        for filename, content in files:
            target = sess_dir / Path(filename).name
            with open(target, "wb") as f:
                f.write(content)
            saved_paths.append((filename, str(target)))
        with self._lock:
            self._sessions[sid] = {
                "dir": str(sess_dir),
                "files": saved_paths,
                "checklist": None,
                "created_at": time.time(),
            }
        return sid

    def attach_checklist(self, sid: str, checklist: MonthlyChecklist) -> None:
        with self._lock:
            sess = self._sessions.get(sid)
            if not sess:
                raise KeyError(sid)
            sess["checklist"] = checklist

    def get(self, sid: str) -> dict:
        with self._lock:
            sess = self._sessions.get(sid)
        if not sess:
            raise HTTPException(status_code=404, detail="セッションが見つかりません")
        return sess

    def delete(self, sid: str) -> None:
        with self._lock:
            sess = self._sessions.pop(sid, None)
        if sess:
            shutil.rmtree(sess["dir"], ignore_errors=True)

    def cleanup_expired(self) -> None:
        cutoff = time.time() - SESSION_TTL_SECONDS
        with self._lock:
            expired = [sid for sid, s in self._sessions.items() if s["created_at"] < cutoff]
        for sid in expired:
            self.delete(sid)


store = SessionStore()


app = FastAPI(title="一括納付明細書チェックリスト作成システム", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Static frontend
STATIC_DIR = Path(__file__).resolve().parent.parent / "frontend"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    index_html = STATIC_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(index_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>一括納付明細書チェックリスト作成システム</h1>")


@app.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/v1/checklist")
async def create_checklist(files: List[UploadFile] = File(...)) -> JSONResponse:
    store.cleanup_expired()
    if not files:
        raise HTTPException(status_code=400, detail="PDFが添付されていません")

    saved: List[tuple] = []
    for f in files:
        if not (f.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail=f"PDF以外のファイルは扱えません: {f.filename}")
        content = await f.read()
        saved.append((f.filename, content))

    sid = store.create(saved)
    sess = store.get(sid)

    docs = []
    for filename, path in sess["files"]:
        docs.append(classify_pdf(filename, path))

    checklist = process_classified_documents(docs)
    store.attach_checklist(sid, checklist)

    return JSONResponse({
        "sessionId": sid,
        "checklist": json.loads(checklist.model_dump_json()),
        "warnings": checklist.warnings,
        "errors": [],
    })


@app.get("/api/v1/checklist/{sid}/markdown", response_class=PlainTextResponse)
def get_markdown(sid: str) -> PlainTextResponse:
    sess = store.get(sid)
    cl: Optional[MonthlyChecklist] = sess.get("checklist")
    if not cl:
        raise HTTPException(404, "チェックリストが未生成です")
    return PlainTextResponse(to_markdown(cl), media_type="text/markdown; charset=utf-8")


@app.get("/api/v1/checklist/{sid}/tsv", response_class=PlainTextResponse)
def get_tsv(sid: str, section: Optional[str] = None) -> PlainTextResponse:
    sess = store.get(sid)
    cl: Optional[MonthlyChecklist] = sess.get("checklist")
    if not cl:
        raise HTTPException(404, "チェックリストが未生成です")
    if section:
        blocks = to_tsv_files(cl)
        if section not in blocks:
            raise HTTPException(400, f"unknown section: {section}")
        return PlainTextResponse(blocks[section], media_type="text/tab-separated-values; charset=utf-8")
    return PlainTextResponse(to_full_tsv(cl), media_type="text/tab-separated-values; charset=utf-8")


@app.get("/api/v1/checklist/{sid}/permits.pdf")
def get_permits_pdf(sid: str) -> FileResponse:
    sess = store.get(sid)
    cl: Optional[MonthlyChecklist] = sess.get("checklist")
    if not cl:
        raise HTTPException(404, "チェックリストが未生成です")
    out_path = Path(sess["dir"]) / "permits_sorted.pdf"
    docs = []
    for filename, path in sess["files"]:
        docs.append(classify_pdf(filename, path))
    permit_count, total_pages = build_sorted_permit_pdf(docs, cl, str(out_path))
    if total_pages == 0:
        raise HTTPException(404, "許可通知書ページが検出できませんでした")
    filename = "許可通知書_抽出.pdf"
    return FileResponse(
        path=str(out_path),
        media_type="application/pdf",
        filename=filename,
    )


@app.delete("/api/v1/checklist/{sid}")
def delete_session(sid: str) -> dict:
    store.delete(sid)
    return {"status": "deleted"}
