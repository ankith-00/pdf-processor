"""
Step 5 — Stamp: insert the barcode image into the QR cell on the PDF page.

Uses PyMuPDF's page.insert_image() to draw the barcode PNG into the detected
QR cell rect. The original layout, fonts, borders, and student photo remain
completely untouched — only the blank QR placeholder cell gets filled.
"""

from __future__ import annotations

import logging

import fitz  # PyMuPDF

logger = logging.getLogger(__name__)


def stamp_barcode(
    page: fitz.Page,
    cell_rect: fitz.Rect,
    barcode_png: bytes,
    margin_pt: float = 5.0,
) -> bool:
    """
    Insert a barcode image into the specified cell on the page.
    
    Args:
        page: The PyMuPDF page to stamp.
        cell_rect: The bounding rect of the QR placeholder cell.
        barcode_png: PNG image bytes of the barcode.
        margin_pt: Margin in points to inset from the cell edges.
    
    Returns:
        True if stamping succeeded, False otherwise.
    """
    try:
        # Inset the rect by the margin to leave some breathing room
        inner_rect = fitz.Rect(
            cell_rect.x0 + margin_pt,
            cell_rect.y0 + margin_pt,
            cell_rect.x1 - margin_pt,
            cell_rect.y1 - margin_pt,
        )

        # Validate the inner rect is still valid (positive width and height)
        if inner_rect.width <= 0 or inner_rect.height <= 0:
            logger.warning(
                f"Page {page.number + 1}: Cell rect too small after margin "
                f"inset ({inner_rect.width:.1f}x{inner_rect.height:.1f}pt). "
                "Using cell rect without margin."
            )
            inner_rect = cell_rect

        # Draw a white background so barcode bars are clearly visible even
        # if the image overflows the original cell boundary slightly
        page.draw_rect(
            inner_rect,
            color=None,           # No border
            fill=(1, 1, 1),       # White fill
            overlay=True,
        )

        # Insert the barcode image — stretch to fill the full width/height
        # of the available rect (no keep_proportion so it uses all the space)
        page.insert_image(
            inner_rect,
            stream=barcode_png,
            keep_proportion=False,  # Fill the rect fully — wider barcode
            overlay=True,           # Draw on top of the white background
        )

        logger.info(
            f"Page {page.number + 1}: Barcode stamped at "
            f"({inner_rect.x0:.1f}, {inner_rect.y0:.1f}, "
            f"{inner_rect.x1:.1f}, {inner_rect.y1:.1f})"
        )
        return True

    except Exception as e:
        logger.error(
            f"Page {page.number + 1}: Failed to stamp barcode: {e}"
        )
        return False
