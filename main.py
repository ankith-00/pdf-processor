"""
FastAPI application — HTTP layer for the hall ticket barcode tool.

Endpoints:
  POST /process       — Upload a PDF, returns { job_id }
  GET  /status/{id}   — Job progress + extracted student data (streamable)
  GET  /result/{id}   — Download the processed PDF

Jobs are stored in an in-process dict with TTL eviction (30 min after completion).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse

from pipeline import Job, JobStatus, process_pdf
from storage import MongoStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Job store ───────────────────────────────────────────────────────────────

_jobs: dict[str, Job] = {}
_JOB_TTL_SECONDS = 30 * 60  # 30 minutes


async def _eviction_loop():
    """Periodically remove completed jobs older than TTL."""
    while True:
        await asyncio.sleep(60)  # Check every minute
        now = time.time()
        expired = [
            jid for jid, job in _jobs.items()
            if job.status in (JobStatus.DONE, JobStatus.ERROR)
            and now - job.created_at > _JOB_TTL_SECONDS
        ]
        for jid in expired:
            logger.info(f"Evicting expired job {jid}")
            del _jobs[jid]


# ─── App lifecycle ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the eviction loop on startup."""
    app.state.mongo = MongoStorage()
    app.state.mongo.mark_processing_jobs_interrupted()
    task = asyncio.create_task(_eviction_loop())
    try:
        yield
    finally:
        task.cancel()
        app.state.mongo.close()


app = FastAPI(
    title="Hall Ticket Barcode Worker",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — allow the Next.js dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Endpoints ───────────────────────────────────────────────────────────────

def _get_job(job_id: str) -> Job | None:
    job = _jobs.get(job_id)
    if job is not None:
        return job

    saved = app.state.mongo.get_job(job_id)
    if saved is None:
        return None

    job = Job(
        job_id=saved["job_id"],
        status=JobStatus(saved["status"]),
        total_pages=saved.get("total_pages", 0),
        processed_pages=saved.get("processed_pages", 0),
        error_message=saved.get("error_message", ""),
        filename=saved.get("filename", ""),
        barcode_success=saved.get("barcode_success", 0),
        barcode_failed=saved.get("barcode_failed", 0),
    )
    job.students = app.state.mongo.get_students(job_id)
    _jobs[job_id] = job
    return job

@app.post("/process")
async def process_upload(file: UploadFile = File(...)):
    """
    Upload a hall ticket PDF for processing.
    
    Returns { job_id } immediately. The PDF is processed in a background
    thread to avoid blocking the event loop.
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are accepted.")

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(400, "Empty file.")

    job_id = str(uuid.uuid4())
    job = Job(job_id=job_id, filename=file.filename or "upload.pdf")
    _jobs[job_id] = job
    app.state.mongo.create_job(job.job_id, job.filename)

    logger.info(f"Received PDF upload: {file.filename} ({len(pdf_bytes)} bytes) → job {job_id}")

    # Run the CPU-heavy PDF processing in a thread pool
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, process_pdf, pdf_bytes, job, app.state.mongo)

    return JSONResponse({"job_id": job_id})


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    """
    Get the current status of a processing job.
    
    Returns progress info and any student data extracted so far.
    The frontend polls this every ~2 seconds to update the UI progressively.
    """
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    return JSONResponse({
        "job_id": job.job_id,
        "status": job.status.value,
        "total_pages": job.total_pages,
        "processed_pages": job.processed_pages,
        "barcode_success": job.barcode_success,
        "barcode_failed": job.barcode_failed,
        "students": job.students,
        "error_message": job.error_message,
        "filename": job.filename,
    })


@app.get("/result/{job_id}")
async def get_result(job_id: str):
    """
    Download the processed PDF with barcodes stamped.
    
    Only available after the job reaches "done" status.
    After download, the result bytes are evicted from memory.
    """
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")

    if job.status == JobStatus.PROCESSING:
        raise HTTPException(202, "Job is still processing.")

    if job.status == JobStatus.ERROR:
        raise HTTPException(500, f"Job failed: {job.error_message}")

    if job.result_pdf is None:
        job.result_pdf = app.state.mongo.get_result(job_id)

    if job.result_pdf is None:
        raise HTTPException(500, "No result PDF available.")

    # Build the output filename
    original = job.filename.rsplit(".", 1)[0] if job.filename else "halltickets"
    out_filename = f"{original}_barcoded.pdf"

    # Return the PDF bytes
    pdf_bytes = job.result_pdf

    # Evict the result from memory after serving (it's been downloaded)
    job.result_pdf = None
    app.state.mongo.delete_result(job_id)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{out_filename}"',
        },
    )


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "active_jobs": len(_jobs)}


@app.get("/lookup/{barcode}")
async def lookup_barcode(barcode: str):
    """Find the extracted student record associated with a barcode value."""
    normalized_barcode = barcode.strip()
    if not normalized_barcode:
        raise HTTPException(400, "Barcode value is required.")

    document = app.state.mongo.find_student_by_barcode(normalized_barcode)
    if document is None:
        raise HTTPException(404, "No student record found for this barcode.")

    return JSONResponse(document)


@app.get("/extracted-data/{job_id}")
async def get_extracted_data(job_id: str):
    """
    All student data extracted for a job, as JSON.

    Works while the job is still processing (returns what has been extracted
    so far — check `status`/`is_complete`) and after it finishes. Each entry
    is one student record plus `barcode_placed`. Data lives in memory only and
    is evicted with the job 30 minutes after completion.
    """
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found (it may have expired — jobs are kept 30 minutes).")

    if job.status == JobStatus.ERROR:
        raise HTTPException(500, f"Job failed: {job.error_message}")

    return JSONResponse({
        "job_id": job.job_id,
        "filename": job.filename,
        "status": job.status.value,
        "is_complete": job.status == JobStatus.DONE,
        "student_count": len(job.students),
        "students": job.students,
    })
