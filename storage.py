"""MongoDB persistence for extracted student records."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

import gridfs
from pymongo import ASCENDING, MongoClient


COLLECTION_NAME = "pdf-processor-data"
JOBS_COLLECTION_NAME = "pdf-processor-jobs"


class MongoStorage:
    def __init__(self) -> None:
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not configured")

        database_name = os.getenv("MONGODB_DATABASE", "ocr-module")
        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.client.admin.command("ping")
        database = self.client[database_name]
        self.collection = database[COLLECTION_NAME]
        self.jobs = database[JOBS_COLLECTION_NAME]
        self.results = gridfs.GridFSBucket(database)
        self.collection.create_index(
            [("job_id", ASCENDING), ("student_index", ASCENDING)],
            unique=True,
        )
        self.jobs.create_index("job_id", unique=True)

    def create_job(self, job_id: str, filename: str) -> None:
        self.jobs.insert_one({
            "job_id": job_id,
            "filename": filename,
            "status": "processing",
            "total_pages": 0,
            "processed_pages": 0,
            "barcode_success": 0,
            "barcode_failed": 0,
            "error_message": "",
            "created_at": datetime.now(timezone.utc),
        })

    def update_job(self, job: Any) -> None:
        self.jobs.update_one(
            {"job_id": job.job_id},
            {"$set": {
                "status": job.status.value,
                "total_pages": job.total_pages,
                "processed_pages": job.processed_pages,
                "barcode_success": job.barcode_success,
                "barcode_failed": job.barcode_failed,
                "error_message": job.error_message,
            }},
        )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self.jobs.find_one({"job_id": job_id}, {"_id": 0})

    def get_students(self, job_id: str) -> list[dict[str, Any]]:
        records = self.collection.find(
            {"job_id": job_id},
            {"_id": 0, "student": 1},
        ).sort("student_index", ASCENDING)
        return [record["student"] for record in records]

    def mark_processing_jobs_interrupted(self) -> None:
        self.jobs.update_many(
            {"status": "processing"},
            {"$set": {
                "status": "error",
                "error_message": "Processing was interrupted by a server restart.",
            }},
        )

    def save_result(self, job_id: str, result_pdf: bytes) -> None:
        file_id = self.results.upload_from_stream(
            f"{job_id}.pdf",
            BytesIO(result_pdf),
            metadata={"job_id": job_id},
        )
        self.jobs.update_one(
            {"job_id": job_id},
            {"$set": {"result_file_id": file_id}},
        )

    def get_result(self, job_id: str) -> bytes | None:
        job = self.get_job(job_id)
        if not job or "result_file_id" not in job:
            return None
        output = BytesIO()
        try:
            self.results.download_to_stream(job["result_file_id"], output)
        except gridfs.errors.NoFile:
            return None
        return output.getvalue()

    def delete_result(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job and "result_file_id" in job:
            try:
                self.results.delete(job["result_file_id"])
            except gridfs.errors.NoFile:
                pass
            self.jobs.update_one(
                {"job_id": job_id},
                {"$unset": {"result_file_id": ""}},
            )
        self.collection.create_index("student.uucms")

    def save_student(
        self,
        job_id: str,
        filename: str,
        student_index: int,
        student: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        self.collection.update_one(
            {"job_id": job_id, "student_index": student_index},
            {
                "$set": {
                    "filename": filename,
                    "student": student,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "job_id": job_id,
                    "student_index": student_index,
                    "created_at": now,
                },
            },
            upsert=True,
        )

    def find_student_by_barcode(self, barcode: str) -> dict[str, Any] | None:
        document = self.collection.find_one(
            {"student.uucms": barcode},
            {"_id": 0, "job_id": 1, "filename": 1, "student": 1},
        )
        return document

    def close(self) -> None:
        self.client.close()