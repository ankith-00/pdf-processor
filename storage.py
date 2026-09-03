"""MongoDB persistence for extracted student records."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient


COLLECTION_NAME = "pdf-processor-data"


class MongoStorage:
    def __init__(self) -> None:
        uri = os.getenv("MONGODB_URI")
        if not uri:
            raise RuntimeError("MONGODB_URI is not configured")

        database_name = os.getenv("MONGODB_DATABASE", "ocr-module")
        self.client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.client.admin.command("ping")
        self.collection = self.client[database_name][COLLECTION_NAME]
        self.collection.create_index(
            [("job_id", ASCENDING), ("student_index", ASCENDING)],
            unique=True,
        )

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

    def close(self) -> None:
        self.client.close()