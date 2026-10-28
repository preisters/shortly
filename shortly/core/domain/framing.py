"""Non-destructive body-asset layout shared by export and preview.

Coordinates are fractions of the 9:16 canvas, measured from the top left.
This is the existing Shorts layout's conservative default, not a measurement
of an arbitrary recent CapCut project. Titles, captions and disclosures stay
outside the body viewport. No source pixels are removed.
"""

BODY_VIEWPORT = {"left": 0.07, "top": 0.30, "width": 0.86, "height": 0.58}


def contain_layout(width: int, height: int) -> tuple[float, float]:
    """CapCut scale relative to canvas-fit source, and normalized center Y."""
    width, height = max(1, width), max(1, height)
    canvas_fit = min(1080 / width, 1920 / height)
    body_fit = min(1080 * BODY_VIEWPORT["width"] / width,
                   1920 * BODY_VIEWPORT["height"] / height)
    center = BODY_VIEWPORT["top"] + BODY_VIEWPORT["height"] / 2
    return round(body_fit / canvas_fit, 8), round(1 - 2 * center, 8)
