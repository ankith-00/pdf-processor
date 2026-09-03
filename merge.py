"""
Step 6 — Merge: finalize the processed PDF into an in-memory buffer.

Since we modify pages in-place on the same fitz.Document object (no actual
split into separate PDFs), the "merge" step is really just saving the modified
document to a BytesIO buffer. This module exists as a clean abstraction point
in case future requirements need actual multi-document merging.
"""

from __future__ import annotations

from io import BytesIO

import fitz  # PyMuPDF


def finalize_pdf(doc: fitz.Document) -> bytes:
    """
    Save the modified document to an in-memory buffer and return the bytes.
    
    Args:
        doc: The PyMuPDF document with barcodes already stamped.
    
    Returns:
        The final PDF as bytes, ready for streaming to the client.
    """
    buffer = BytesIO()

    # Save with garbage collection to minimize file size
    # (removes unused objects from the modification process)
    doc.save(
        buffer,
        garbage=4,          # Maximum garbage collection
        deflate=True,       # Compress streams
        clean=True,         # Clean content streams
    )

    return buffer.getvalue()
