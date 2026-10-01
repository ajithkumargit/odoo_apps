"""Offline supplier-bill OCR and deterministic field extraction.

Tamil and English image OCR runs locally through PaddleOCR when installed,
with Tesseract as a fallback. Fitz / PyMuPDF handles PDF
text extraction and rendering. No bill content is sent to an external service.
"""

from __future__ import annotations

import hashlib
import itertools
import logging
import re
import threading
import time
from contextvars import ContextVar
from functools import lru_cache


_logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 5
MAX_IMAGE_SIDE = 3400
TARGET_IMAGE_WIDTH = 2100
MIN_OCR_SCORE = 0.45
OCR_PAGE_BUDGET_SECONDS = 145
OCR_FILE_BUDGET_SECONDS = 210

_OCR_LOCK = threading.Lock()
_HEADER_ALIASES = ContextVar("shop_ocr_header_aliases", default={})
_OCR_TEMPLATE = ContextVar("shop_ocr_template", default=None)
_METADATA_ALIAS_ROLES = {"bill_number", "bill_date"}


@lru_cache(maxsize=1)
def _get_paddle_backend():
    if __package__:
        from . import local_paddle_backend
        return local_paddle_backend
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "shop_local_paddle_backend", Path(__file__).with_name("local_paddle_backend.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_GSTIN_RE = re.compile(r"\d{2}[A-Z]{5}\d{4}[A-Z]\d[Z2][A-Z0-9]")
_DATE_RE = re.compile(r"\b([0-3]?\d)[./-]([01]?\d)[./-]((?:19|20)?\d{2})\b")
_TEXT_DATE_RE = re.compile(
    r"\b([0-3]?\d)[./-](JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*[./-]((?:19|20)?\d{2})\b",
    re.IGNORECASE,
)
_BILL_NUMBER_RE = re.compile(
    r"\b(?:B[I1L]{2,3}|INVOICE)\s*(?:NO|NUMBER|#)\s*[:\-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-]{2,})\b",
    re.IGNORECASE,
)


class LocalOCRError(Exception):
    """A user-correctable local OCR failure."""


@lru_cache(maxsize=1)
def _get_tesseract_backend():
    # Also support the standalone diagnostic runner without importing Odoo.
    if __package__:
        from . import local_ocr_backend
        return local_ocr_backend
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "shop_local_ocr_backend", Path(__file__).with_name("local_ocr_backend.py")
    )
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    return backend


def _normalise_space(value):
    return re.sub(r"\s+", " ", (value or "").replace("\x00", " ")).strip()


def _normalise_key(value):
    # Tamil vowel signs are combining marks: isalnum() alone would discard
    # them and collapse distinct labels/names to the same incomplete key.
    import unicodedata

    text = unicodedata.normalize("NFC", (value or "").upper())
    return "".join(
        character
        for character in text
        if character == "%" or unicodedata.category(character)[0] in "LMN"
    )


def _has_tamil(text):
    return any("\u0b80" <= character <= "\u0bff" for character in (text or ""))


def _box_values(box):
    xs = [float(point[0]) for point in box]
    ys = [float(point[1]) for point in box]
    return min(xs), min(ys), max(xs), max(ys)


def _token(text, score, box, page=1, source="local-ocr"):
    x0, y0, x1, y1 = _box_values(box)
    token = {
        "text": _normalise_space(text),
        "score": float(score),
        "x0": x0,
        "y0": y0,
        "x1": x1,
        "y1": y1,
        "xc": (x0 + x1) / 2,
        "yc": (y0 + y1) / 2,
        "page": page,
        "source": source,
    }
    if x1 - x0 > (y1 - y0) * 2:
        slopes = []
        for first, second in ((box[0], box[1]), (box[3], box[2])):
            dx = float(second[0]) - float(first[0])
            if dx > 1:
                slopes.append((float(second[1]) - float(first[1])) / dx)
        if slopes:
            token["baseline_slope"] = sum(slopes) / len(slopes)
    return token


def _order_quad(points):
    import numpy as np

    points = np.asarray(points, dtype="float32").reshape(4, 2)
    ordered = np.zeros((4, 2), dtype="float32")
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).reshape(-1)
    ordered[0] = points[sums.argmin()]
    ordered[2] = points[sums.argmax()]
    ordered[1] = points[differences.argmin()]
    ordered[3] = points[differences.argmax()]
    return ordered


def _crop_and_rectify_document(image):
    """Crop a photographed sheet and correct perspective when safely detected."""
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    scale = min(1.0, 1200.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(gray, 45, 140)
    edges = cv2.dilate(edges, None, iterations=1)
    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    image_area = small.shape[0] * small.shape[1]
    quad = None
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:20]:
        if cv2.contourArea(contour) < image_area * 0.35:
            break
        perimeter = cv2.arcLength(contour, True)
        approximation = cv2.approxPolyDP(contour, 0.025 * perimeter, True)
        if len(approximation) == 4 and cv2.isContourConvex(approximation):
            quad = approximation.reshape(4, 2) / scale
            break

    if quad is None:
        return image

    top_left, top_right, bottom_right, bottom_left = _order_quad(quad)
    target_width = int(
        max(
            np.linalg.norm(bottom_right - bottom_left),
            np.linalg.norm(top_right - top_left),
        )
    )
    target_height = int(
        max(
            np.linalg.norm(top_right - bottom_right),
            np.linalg.norm(top_left - bottom_left),
        )
    )
    if target_width < 400 or target_height < 500:
        return image

    destination = np.array(
        [
            [0, 0],
            [target_width - 1, 0],
            [target_width - 1, target_height - 1],
            [0, target_height - 1],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(
        _order_quad(quad).astype("float32"), destination
    )
    return cv2.warpPerspective(
        image,
        matrix,
        (target_width, target_height),
        borderMode=cv2.BORDER_REPLICATE,
    )


def _resize_for_ocr(image, target_width=TARGET_IMAGE_WIDTH, max_side=MAX_IMAGE_SIDE):
    import cv2

    height, width = image.shape[:2]
    # Preserve small print in phone photos. Upscaling helps segmentation but
    # cannot recover missing detail, so cap enlargement and memory usage.
    # Very large phone photos are both slower and less accurate in Tesseract:
    # at 3500-4500px wide, grid remnants split compact headings such as
    # ``Item Name`` and ``CGST %`` into noise. Keep enough pixels for small
    # print, while also normalising oversized captures to the trained OCR DPI.
    scale = min(3.0, target_width / max(width, 1))
    if max(height * scale, width * scale) > max_side:
        scale = max_side / max(height, width)
    if 0.90 <= scale <= 1.10 and max(height, width) <= max_side:
        return image
    interpolation = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=interpolation)


def _deskew_image(image):
    """Correct a small, well-supported table/text skew without clipping edges."""
    import math
    import cv2
    import numpy as np

    height, width = image.shape[:2]
    scale = min(1.0, 1400.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    lines = cv2.HoughLinesP(
        cv2.Canny(gray, 50, 150), 1, math.pi / 720,
        threshold=75, minLineLength=max(80, int(small.shape[1] * .20)),
        maxLineGap=12,
    )
    angles = []
    for detected in lines if lines is not None else []:
        x0, y0, x1, y1 = detected[0]
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
        angle = (angle + 90) % 180 - 90
        if abs(angle) <= 12:
            angles.append(angle)
    if len(angles) < 4:
        return image
    angle = float(np.median(angles))
    supported = sum(abs(value - angle) < 1.5 for value in angles)
    if abs(angle) < .4 or supported < max(4, len(angles) * .65):
        return image
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sine + width * cosine)
    new_height = int(height * cosine + width * sine)
    matrix[0, 2] += (new_width - width) / 2
    matrix[1, 2] += (new_height - height) / 2
    return cv2.warpAffine(
        image, matrix, (new_width, new_height),
        flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255),
    )


def _preprocess_variants(image, rectify=True, deskew=True):
    import cv2
    import numpy as np

    working = _crop_and_rectify_document(image) if rectify else image
    if deskew:
        working = _deskew_image(working)
    working = _resize_for_ocr(working)
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    enhanced = cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)

    # Dense invoice grids can be detected as thousands of tiny OCR regions.
    # Remove only long horizontal/vertical rules and retain the printed text.
    inverted = cv2.adaptiveThreshold(
        clahe,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        35,
        13,
    )
    height, width = clahe.shape[:2]
    horizontal = cv2.morphologyEx(
        inverted,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(100, width // 12), 1)),
    )
    vertical = cv2.morphologyEx(
        inverted,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(80, height // 12))),
    )
    rules = cv2.bitwise_or(horizontal, vertical)
    # Photographed grids are not axis-aligned, even after deskew: perspective
    # makes individual rules diverge. Detect long sloping rules as well.
    segments = cv2.HoughLinesP(
        inverted, 1, np.pi / 1800, threshold=max(80, width // 12),
        minLineLength=max(150, min(width, height) // 5), maxLineGap=12,
    )
    if segments is not None:
        for x0, y0, x1, y1 in segments[:, 0]:
            dx, dy = abs(int(x1) - int(x0)), abs(int(y1) - int(y0))
            if (dx > width * .18 and dy < dx * .15) or (dy > height * .2 and dx < dy * .15):
                cv2.line(rules, (x0, y0), (x1, y1), 255, max(2, width // 900))
    rules = cv2.dilate(rules, np.ones((2, 2), dtype="uint8"), iterations=1)
    degridded = clahe.copy()
    degridded[rules > 0] = 255
    return enhanced, cv2.cvtColor(degridded, cv2.COLOR_GRAY2BGR)


def _likely_sideways(image):
    """Detect a photographed table whose long row rules run vertically."""
    import cv2
    import math

    height, width = image.shape[:2]
    scale = min(1.0, 900.0 / max(height, width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        math.pi / 180,
        threshold=65,
        minLineLength=max(60, int(min(small.shape[:2]) * 0.22)),
        maxLineGap=18,
    )
    horizontal_length = 0.0
    vertical_length = 0.0
    for detected in lines if lines is not None else []:
        x0, y0, x1, y1 = detected[0]
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        angle = abs(math.degrees(math.atan2(dy, dx))) % 180
        angle = min(angle, 180 - angle)
        if angle <= 12:
            horizontal_length += length
        elif angle >= 78:
            vertical_length += length
    return (
        vertical_length > horizontal_length * 1.35
        and vertical_length > min(small.shape[:2]) * 2
    )


def _orientation_variants(image, suggested_rotation=None):
    import cv2

    rotations = [0, 270, 90, 180]
    height, width = image.shape[:2]
    if (suggested_rotation is None and max(height, width) / min(height, width) >= 1.4
            and _likely_sideways(image)):
        rotations = [270, 90, 0, 180]
    if suggested_rotation in rotations:
        rotations.remove(suggested_rotation)
        rotations.insert(0, suggested_rotation)
    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180,
             270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    for degrees in rotations:
        yield (
            "original orientation" if not degrees else f"auto-rotate {degrees} degrees",
            cv2.rotate(image, codes[degrees]) if degrees else image,
        )


def _intersection_over_union(first, second):
    x0 = max(first["x0"], second["x0"])
    y0 = max(first["y0"], second["y0"])
    x1 = min(first["x1"], second["x1"])
    y1 = min(first["y1"], second["y1"])
    intersection = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    first_area = max(1.0, first["x1"] - first["x0"]) * max(
        1.0, first["y1"] - first["y0"]
    )
    second_area = max(1.0, second["x1"] - second["x0"]) * max(
        1.0, second["y1"] - second["y0"]
    )
    return intersection / max(first_area + second_area - intersection, 1.0)


def _merge_ocr_passes(primary, secondary):
    """Keep the clearest reading of overlapping words and add missed words."""
    merged = list(primary)
    for candidate in secondary:
        match_index = None
        match_overlap = 0.0
        for index, current in enumerate(merged):
            overlap = _intersection_over_union(current, candidate)
            if overlap > match_overlap:
                match_index = index
                match_overlap = overlap
        if match_index is None or match_overlap < 0.35:
            if candidate["score"] >= 0.62:
                merged.append(candidate)
            continue
        current = merged[match_index]
        current_key = _normalise_key(current["text"])
        candidate_key = _normalise_key(candidate["text"])
        candidate_is_better = candidate["score"] > current["score"] + 0.025
        candidate_is_better |= (
            candidate_key.isdigit()
            and not current_key.isdigit()
            and candidate["score"] >= current["score"] - 0.03
        )
        if candidate_is_better:
            merged[match_index] = candidate
    return sorted(merged, key=lambda item: (item["page"], item["yc"], item["xc"]))


def _image_from_bytes(file_bytes):
    import cv2
    import numpy as np
    from PIL import Image, ImageOps
    from io import BytesIO

    try:
        with Image.open(BytesIO(file_bytes)) as source:
            source.seek(0)
            rgb = ImageOps.exif_transpose(source).convert("RGB")
            return cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    except Exception as error:
        raise LocalOCRError("The uploaded image could not be opened.") from error


def _pdf_pages(file_bytes):
    import cv2
    import numpy as np

    try:
        import pymupdf as fitz
    except ImportError as error:  # pragma: no cover - depends on deployment
        raise LocalOCRError("PyMuPDF is not installed; PDF bills cannot be opened.") from error

    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as error:
        raise LocalOCRError("The uploaded PDF could not be opened.") from error
    try:
        if document.page_count > MAX_PDF_PAGES:
            raise LocalOCRError(
                "The PDF has too many pages. Upload a bill with at most %s pages."
                % MAX_PDF_PAGES
            )
        pages = []
        for page_number, page in enumerate(document, start=1):
            native_tokens = []
            for word in page.get_text("words", sort=False):
                x0, y0, x1, y1, text = word[:5]
                native_tokens.append(
                    _token(
                        text,
                        1.0,
                        [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
                        page=page_number,
                        source="fitz-native-text",
                    )
                )
            # A scan may have a small, incomplete text overlay. Trust native
            # text without OCR only when there are no embedded raster images.
            has_images = bool(page.get_images())
            native_parsed = _parse_page(native_tokens, page.rect.width, page.rect.height)
            if native_parsed["lines"] and not has_images:
                image = None
            else:
                scale = min(250 / 72, MAX_IMAGE_SIDE / max(page.rect.width, page.rect.height))
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(scale, scale), colorspace=fitz.csRGB, alpha=False
                )
                image = cv2.cvtColor(
                    np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                        pixmap.height, pixmap.width, 3
                    ), cv2.COLOR_RGB2BGR,
                )
            pages.append((image, native_tokens))
        return pages
    finally:
        document.close()


def _median_token_height(tokens):
    heights = sorted(max(1.0, token["y1"] - token["y0"]) for token in tokens)
    return heights[len(heights) // 2] if heights else 12.0


def _group_lines(tokens):
    """Rebuild visual lines while retaining left-to-right ordering."""
    if not tokens:
        return []
    tolerance = max(5.0, _median_token_height(tokens) * 0.70)
    lines = []
    for token in sorted(tokens, key=lambda item: (item["yc"], item["xc"])):
        best = None
        best_distance = None
        for line in lines[-5:]:
            distance = abs(line["yc"] - token["yc"])
            if distance <= tolerance and (best_distance is None or distance < best_distance):
                best = line
                best_distance = distance
        if best is None:
            lines.append({"yc": token["yc"], "tokens": [token]})
        else:
            best["tokens"].append(token)
            best["yc"] = sum(item["yc"] for item in best["tokens"]) / len(best["tokens"])
    for line in lines:
        line["tokens"].sort(key=lambda item: item["xc"])
        line["text"] = _normalise_space(" ".join(item["text"] for item in line["tokens"]))
    return sorted(lines, key=lambda item: item["yc"])


def _parse_float(value):
    cleaned = (value or "").replace(",", "").replace("₹", "")
    matches = re.findall(r"-?\d+(?:\.\d+)?", cleaned)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _numeric_values(tokens):
    values = []
    for token in sorted(tokens, key=lambda item: (item["yc"], item["xc"])):
        value = _parse_float(token["text"])
        if value is not None:
            values.append(value)
    return values


def _last_number(tokens, default=0.0):
    values = _numeric_values(tokens)
    non_zero = [value for value in values if value]
    return non_zero[-1] if non_zero else (values[-1] if values else default)


def _parse_quantity(tokens):
    text = " ".join(token["text"] for token in sorted(tokens, key=lambda item: (item["yc"], item["xc"])))
    match = re.search(r"(\d+(?:\.\d+)?)\s*[+]\s*(\d+(?:\.\d+)?)", text)
    if match:
        return float(match.group(1)), float(match.group(2))
    return max(_last_number(tokens), 0.0), 0.0


def _gst_rate(tokens):
    values = _numeric_values(tokens)
    common_rates = (0.1, 0.25, 3.0, 5.0, 12.0, 18.0, 28.0)
    for value in values:
        nearest = min(common_rates, key=lambda rate: abs(value - rate))
        tolerance = 0.005 if nearest < 1 else 0.011
        if abs(value - nearest) <= tolerance:
            return nearest
    return 0.0


def _median(values, default=0.0):
    ordered = sorted(values)
    if not ordered:
        return default
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _complete_row_anchors(anchors, default_spacing):
    """Infer occasional serial numbers missed by OCR from neighbouring rows."""
    if not anchors:
        return []
    observed_spacings = []
    for first, second in zip(anchors, anchors[1:]):
        number_gap = second["number"] - first["number"]
        if 0 < number_gap <= 5:
            observed_spacings.append((second["y"] - first["y"]) / number_gap)
    spacing = _median(
        [value for value in observed_spacings if value > 0], default_spacing
    )
    completed = []
    first = anchors[0]
    if 1 < first["number"] <= 5:
        for number in range(1, first["number"]):
            completed.append(
                {
                    "number": number,
                    "y": first["y"] - spacing * (first["number"] - number),
                    "x": first.get("x"),
                    "inferred": True,
                }
            )
    for index, anchor in enumerate(anchors):
        completed.append(anchor)
        if index + 1 >= len(anchors):
            continue
        following = anchors[index + 1]
        number_gap = following["number"] - anchor["number"]
        y_gap = following["y"] - anchor["y"]
        geometric_gap = (
            max(1, round(y_gap / spacing))
            if spacing and y_gap > spacing * 1.7
            else 1
        )
        sequence_gap = number_gap if 1 < number_gap <= 5 else 1
        steps = max(sequence_gap, geometric_gap)
        if 1 < steps <= 5:
            y_step = y_gap / steps
            for offset in range(1, steps):
                completed.append(
                    {
                        "number": (
                            anchor["number"] + offset
                            if sequence_gap == steps
                            else anchor["number"]
                        ),
                        "y": anchor["y"] + y_step * offset,
                        "x": (
                            anchor.get("x")
                            + (following.get("x") - anchor.get("x"))
                            * offset
                            / steps
                            if anchor.get("x") is not None
                            and following.get("x") is not None
                            else anchor.get("x")
                        ),
                        "inferred": True,
                    }
                )
    return sorted(completed, key=lambda item: item["y"])


def _estimate_row_slope(tokens, anchors, serial_x, row_spacing, top, bottom, serial_right):
    """Estimate camera skew so words are compared on the same row baseline."""
    if len(anchors) < 2:
        return 0.0
    relevant = [
        token
        for token in tokens
        if top < token["yc"] < bottom and token["xc"] > serial_right
    ]
    observed = [t["baseline_slope"] for t in relevant
                if "baseline_slope" in t and abs(t["baseline_slope"]) <= .3]
    centre = _median(observed) if len(observed) >= 4 else 0.0
    tolerance = max(5.0, row_spacing * 0.30)
    best_slope = 0.0
    best_score = -1.0
    for step in range(-30, 31):
        slope = centre + step * 0.002
        if len(observed) >= 4 and abs(slope - centre) > .016:
            continue
        score = 0.0
        for token in relevant:
            adjusted_y = token["yc"] - slope * (token["xc"] - serial_x)
            distance = min(abs(adjusted_y - anchor["y"]) for anchor in anchors)
            if distance <= tolerance:
                score += token["score"] * (1.0 - distance / (tolerance * 2))
        if score > best_score:
            best_score, best_slope = score, slope
    return best_slope


def _estimate_local_row_slope(
    tokens,
    anchor,
    row_spacing,
    table_left,
    table_right,
    preferred_slope=None,
):
    """Follow a single perspective-distorted row across the invoice."""
    anchor_x = anchor.get("x") or table_left
    relevant = [
        token
        for token in tokens
        if table_left <= token["xc"] <= table_right
        and abs(token["yc"] - anchor["y"]) <= row_spacing * 3
    ]
    observed = [t["baseline_slope"] for t in relevant
                if "baseline_slope" in t and abs(t["baseline_slope"]) <= .3]
    measured_slope = _median(observed) if len(observed) >= 3 else None
    tolerance = max(4.0, row_spacing * 0.30)
    best_slope = 0.0
    best_score = -1.0
    for step in range(-15, 46):
        slope = (measured_slope or 0) + step * 0.002
        if measured_slope is not None and abs(slope - measured_slope) > .012:
            continue
        if measured_slope is None and preferred_slope is not None and abs(slope - preferred_slope) > 0.014:
            continue
        score = 0.0
        for token in relevant:
            expected_y = anchor["y"] + slope * (token["xc"] - anchor_x)
            distance = abs(token["yc"] - expected_y)
            if distance <= tolerance:
                score += token["score"] * (1.0 - distance / (tolerance * 2))
        if score > best_score:
            best_score, best_slope = score, slope
    return best_slope


_TAMIL_BILL_NUMBER_RE = re.compile(
    r"(?:பில்|விலைப்பட்டியல்|விற்பனைப்பட்டியல்|ரசீது|இரசீது)\s*"
    r"(?:எண்|நம்பர்|NO\.?|NUMBER|#)\s*[.:\-]?\s*"
    r"([A-Z0-9][A-Z0-9/\-]*)\b",
    re.IGNORECASE,
)


def _header_value(lines, pattern):
    for line in lines:
        match = pattern.search(line["text"])
        if match:
            return _normalise_space(match.group(1)).strip(":-")
    return None


def _bill_number(lines):
    if (_OCR_TEMPLATE.get() or {}).get("strict"):
        return None
    candidates = []
    texts = [line["text"] for line in lines]
    texts.append(" ".join(texts))
    for line_index, text in enumerate(texts):
        for pattern in (_BILL_NUMBER_RE, _TAMIL_BILL_NUMBER_RE):
            for match in pattern.finditer(text):
                value = _normalise_space(match.group(1)).strip(":-")
                key = _normalise_key(value)
                if key in ("BILL", "DATE", "NUMBER", "NO") or not any(
                    character.isdigit() for character in key
                ):
                    continue
                score = sum(character.isdigit() for character in key) * 4
                score += sum(character.isalpha() for character in key)
                score -= line_index * 0.05
                candidates.append((score, value))
    value = max(candidates, default=(0, None), key=lambda item: item[0])[1]
    if value:
        # Dot-matrix text often turns a capital I in a three-letter prefix
        # into 1 (for example AAI17572 -> AA117572).
        value = re.sub(r"^([A-Z]{2})[1T](?=\d{5,}$)", r"\1I", value, flags=re.IGNORECASE)
    return value


def _bill_number_from_tokens(tokens):
    median_height = _median_token_height(tokens)
    template = _OCR_TEMPLATE.get() or {}
    if template.get("strict"):
        configured = {
            _normalise_key(label)
            for label in (_HEADER_ALIASES.get() or {}).get("bill_number", [])
        }
        labels = [token for token in tokens if _normalise_key(token["text"]) in configured]
    else:
        labels = []
        for token in tokens:
            key = _normalise_key(token["text"])
            if re.fullmatch(r"B[I1L]{2,3}(?:NO|NUMBER)?", key) or key in (
            "INVOICENO",
            "INVOICENUMBER",
            "பில்",
            "பில்எண்",
            "விலைப்பட்டியல்எண்",
            "விற்பனைப்பட்டியல்எண்",
            "ரசீதுஎண்",
            "இரசீதுஎண்",
            ):
                labels.append(token)
    candidates = []
    for label in labels:
        for token in tokens:
            if token is label:
                continue
            if token["x0"] < label["x0"] or abs(token["yc"] - label["yc"]) > median_height * 3:
                continue
            key = _normalise_key(token["text"])
            if len(key) < 3 or not any(character.isdigit() for character in key):
                continue
            if key.startswith(("DATE", "GST", "PAN", "FSSAI", "தேதி", "நாள்", "ஜிஎஸ்டி")):
                continue
            if _DATE_RE.search(token["text"]) or re.search(r"\d{1,2}:\d{2}", token["text"]):
                continue
            distance = abs(token["yc"] - label["yc"]) + max(
                0.0, token["x0"] - label["x1"]
            ) * 0.1
            score = 20.0 + min(len(key), 15) * 0.2
            if any(character.isalpha() for character in key):
                score += 3.0
            score -= (distance / max(median_height, 1.0)) * 8.0
            candidates.append((score, token["text"].strip(":- ")))
    value = max(candidates, default=(0, None), key=lambda item: item[0])[1]
    if value:
        value = re.sub(r"^([A-Z]{2})[1T](?=\d{5,}$)", r"\1I", value, flags=re.IGNORECASE)
    return value


def _gst_from_tax_amounts(tokens):
    values = [value for value in _numeric_values(tokens) if value > 0]
    common_rates = (3.0, 5.0, 12.0, 18.0, 28.0)
    candidates = []
    for index, first in enumerate(values):
        for second in values[index + 1 :]:
            if abs(first - second) > max(0.08, first * 0.012):
                continue
            tax_component = (first + second) / 2
            for taxable in values:
                if taxable <= tax_component * 2:
                    continue
                calculated = (tax_component * 2 / taxable) * 100
                nearest = min(common_rates, key=lambda rate: abs(rate - calculated))
                error = abs(nearest - calculated)
                if error <= 1.0:
                    candidates.append((error, nearest))
    return min(candidates, default=(999.0, 0.0), key=lambda item: item[0])[1]


def _bill_date(lines):
    template = _OCR_TEMPLATE.get() or {}
    if template.get("strict"):
        configured = {
            _normalise_key(label)
            for label in (_HEADER_ALIASES.get() or {}).get("bill_date", [])
        }
        parsed_date = None
        for index, line in enumerate(lines[:35]):
            line_key = _normalise_key(line["text"])
            if not any(label and label in line_key for label in configured):
                continue
            for candidate in lines[index:index + 3]:
                parsed_date = _parse_date_value(candidate["text"])
                if parsed_date:
                    break
            if parsed_date:
                break
        return parsed_date
    labelled = re.compile(
        r"(?:(?:B[I1L]{2,3}|INVOICE)\s*DATE|"
        r"(?:(?:பில்|விலைப்பட்டியல்|ரசீது)\s*)?(?:தேதி|நாள்))\s*[:\-]?\s*"
        r"([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
        re.IGNORECASE,
    )
    raw_date = _header_value(lines, labelled)
    match = _DATE_RE.search(raw_date or "")
    if not match:
        for line in lines[:25]:
            if any(label in line["text"].upper() for label in ("DATE", "தேதி", "நாள்")):
                match = _DATE_RE.search(line["text"])
                if match:
                    break
    if not match:
        # Perspective can put the label and value on separate reconstructed
        # lines even though Tesseract read the date token correctly.
        for line in lines[:50]:
            match = _DATE_RE.search(line["text"])
            if match:
                break
    if not match:
        return None
    day, month, year = match.groups()
    if len(year) == 2:
        year = "20" + year
    try:
        from datetime import date

        day_number, month_number, year_number = int(day), int(month), int(year)
        return date(year_number, month_number, day_number).isoformat()
    except ValueError:
        return None


def _parse_date_value(text):
    from datetime import date, datetime

    match = _DATE_RE.search(text or "")
    if match:
        day, month, year = match.groups()
        if len(year) == 2:
            year = "20" + year
        try:
            return date(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None
    match = _TEXT_DATE_RE.search(text or "")
    if not match:
        return None
    day, month_name, year = match.groups()
    if len(year) == 2:
        year = "20" + year
    try:
        return datetime.strptime(
            "%s-%s-%s" % (day, month_name[:3], year), "%d-%b-%Y"
        ).date().isoformat()
    except ValueError:
        return None


def _vendor_name(lines, page_width):
    template = _OCR_TEMPLATE.get() or {}
    if template.get("strict"):
        return template.get("vendor_name") or None
    exclusions = (
        "TAX INVOICE",
        "INVOICE",
        "PAGE ",
        "STATE",
        "GST",
        "PAN ",
        "FSSAI",
        "BILL ",
        "ADDRESS",
        "PHONE",
        "MOBILE",
        "விலைப்பட்டியல்",
        "பில் எண்",
        "ரசீது எண்",
        "முகவரி",
        "தொலைபேசி",
        "கைபேசி",
        "தேதி",
        "மாநிலம்",
        "ஜிஎஸ்டி",
    )
    best_text = None
    best_score = -1
    for index, line in enumerate(lines[:18]):
        left_tokens = [token for token in line["tokens"] if token["xc"] < page_width * 0.58]
        text = _normalise_space(" ".join(token["text"] for token in left_tokens))
        upper = text.upper()
        alpha_count = sum(character.isalpha() for character in text)
        if alpha_count < 5 or any(exclusion in upper for exclusion in exclusions):
            continue
        score = alpha_count - index
        if re.search(r"AGENC|DISTRIB|TRADER|ENTERPRISE|SUPPL|STORE|MART", upper):
            score += 45
        if any(word in text for word in ("ஏஜென்சி", "ஸ்டோர்", "டிரேடர்", "மளிகை", "நிறுவனம்", "கடை", "வணிகம்")):
            score += 45
        if re.search(r"NAGAR|ROAD|STREET|TAMIL|KERALA|\bDAM\b", upper):
            score -= 25
        if any(word in text for word in ("நகர்", "சாலை", "தெரு", "தமிழ்நாடு", "மாவட்டம்")):
            score -= 25
        if score > best_score:
            best_text, best_score = text, score
    return best_text


def _configured_header_role(text):
    raw = (text or "").strip()
    key = _normalise_key(raw)
    for role, labels in (_HEADER_ALIASES.get() or {}).items():
        if role in _METADATA_ALIAS_ROLES:
            continue
        for label in labels:
            configured_raw = str(label or "").strip()
            configured_key = _normalise_key(configured_raw)
            if (key and key == configured_key) or (
                not key and raw and raw == configured_raw
            ):
                return role
    return None


def _table_header_role(text):
    """Recognise printed column labels without translating product names."""
    key = _normalise_key(text)
    configured_role = _configured_header_role(text)
    if configured_role:
        return configured_role
    if (_OCR_TEMPLATE.get() or {}).get("strict"):
        return None
    # Some invoices print both a tax-inclusive list rate and the actual
    # tax-exclusive purchase rate. Keep them as separate columns so their
    # centres are not averaged together.
    if "RATE" in key and "INCL" in key and "TAX" in key:
        return "tax_inclusive_rate"
    aliases = {
        "serial": ("SL", "S1", "SI", "SLNO", "SR", "SRNO", "SNO", "வஎண்", "வரிசைஎண்"),
        "description": (
            "பொருள்", "பொருட்கள்", "பொருள்பெயர்", "பொருட்பெயர்", "பொருளின்பெயர்",
            "பொருட்களின்பெயர்", "விபரம்", "விவரம்", "பொருள்விவரம்", "சரக்குவிவரம்",
            "ITEM", "PARTICULARS",
        ),
        "quantity": ("QTY", "QUANTITY", "அளவு", "எண்ணிக்கை", "எடை", "நிறை"),
        "rate": ("RATE", "PRICE", "UNITPRICE", "விலை", "விகிதம்", "தனிவிலை", "ஒன்றின்விலை"),
        "case": ("CS", "CASE", "CASES", "பெட்டி", "பெட்டிகள்"),
        "mrp": ("எம்ஆர்பி",),
        "scheme_discount": ("தள்ளுபடிதொகை",),
        "free_quantity": ("FREE", "FREEQTY", "FQTY", "இலவசம்"),
        "cash_discount": ("CD", "CDAMT", "CDAMOUNT", "CASHDISCOUNT"),
        "discount_percent": ("DIS%", "DISC%", "DISCOUNT%", "தள்ளுபடி%", "தள்ளுபடிசதவீதம்"),
        "taxable": ("வரிக்குரியதொகை", "வரிவிதிக்கத்தக்கதொகை", "வரிக்குமுன்தொகை"),
        "gst": ("GST", "GST%", "TAX%", "வரி%", "வரிசதவீதம்", "ஜிஎஸ்டி", "ஜிஎஸ்டி%"),
        "tax_amount": ("TAX", "TAXAMT", "TAXAMOUNT", "GSTAMT", "GSTAMOUNT", "வரி", "வரித்தொகை", "வரிதொகை"),
        "cgst_percent": ("CGST%", "சிஜிஎஸ்டி%"),
        "sgst_percent": ("SGST%", "எஸ்ஜிஎஸ்டி%"),
        "igst_percent": ("IGST%", "ஐஜிஎஸ்டி%"),
        "cgst": ("சிஜிஎஸ்டி",),
        "sgst": ("எஸ்ஜிஎஸ்டி",),
        "igst": ("IGST", "IGSTAMT", "ஐஜிஎஸ்டி"),
        "net": ("TOTAL", "AMOUNT", "AMT", "தொகை", "மொத்தம்", "மொத்ததொகை", "நிகரதொகை"),
    }
    for role, values in aliases.items():
        if key in values:
            return role
    if "HSN" in key:
        return "hsn"
    if any(marker in key for marker in ("PRODUCT", "DESCRIPTION", "ITEMNAME")):
        return "description"
    if "BARCODE" in key or key == "EAN":
        return "barcode"
    if "UPC" in key:
        return "upc"
    if "MRP" in key:
        return "mrp"
    if "PCS" in key:
        return "quantity"
    if "BASE" in key or key == "UNITRATE":
        return "rate"
    if "SCH" in key:
        return "scheme_discount"
    if key.startswith("RS") and "DISC" in key:
        return "cash_discount"
    if "TAXABLE" in key:
        return "taxable"
    if key == "NETRATE":
        return "tax_inclusive_rate"
    if "CGST" in key or key == "OGST":
        return "cgst"
    if any(marker in key for marker in ("SGST", "UTGST", "UIGST")):
        return "sgst"
    if key.startswith("NET"):
        return "net"
    return None


def _join_table_header_tokens(tokens, median_height):
    """Join adjacent label fragments, including a separately OCR'd percent sign."""
    tokens = [token for line in _group_lines(tokens) for token in line["tokens"]]
    # OCR often returns a long heading as three words (or as two chunks,
    # e.g. "Product Full" + "Name"). The pairwise join below cannot make
    # those chunks into a configured heading, so recover the whole alias first.
    long_aliases = {
        _normalise_key(label)
        for labels in (_HEADER_ALIASES.get() or {}).values()
        for label in labels
        if len(str(label).split()) >= 3
    }
    if long_aliases:
        joined = []
        used = set()
        for index, token in enumerate(tokens):
            if index in used:
                continue
            parts = [token]
            key = _normalise_key(token["text"])
            for following_index in range(index + 1, min(index + 4, len(tokens))):
                following = tokens[following_index]
                if following_index in used or not any(alias.startswith(key) for alias in long_aliases):
                    break
                previous = parts[-1]
                if (abs(following["yc"] - token["yc"]) > median_height * 0.8
                        or following["x0"] < previous["x0"]
                        or following["x0"] - previous["x1"] > median_height * 2):
                    break
                parts.append(following)
                key += _normalise_key(following["text"])
                if key in long_aliases:
                    break
            if len(parts) > 1 and key in long_aliases:
                used.update(range(index, index + len(parts)))
                x0, x1 = min(part["x0"] for part in parts), max(part["x1"] for part in parts)
                y0, y1 = min(part["y0"] for part in parts), max(part["y1"] for part in parts)
                joined.append({**token, "text": " ".join(part["text"] for part in parts),
                               "x0": x0, "x1": x1, "y0": y0, "y1": y1,
                               "xc": (x0 + x1) / 2, "yc": (y0 + y1) / 2})
            else:
                joined.append(token)
                used.add(index)
        tokens = joined
    merged = []
    consumed = set()
    suffixes = {"%", "AMT", "AMOUNT", "தொகை", "பெயர்", "NAME", "PRICE", "RATE"}
    for index, token in enumerate(tokens):
        if index in consumed:
            continue
        match = None
        for following_index, following in enumerate(tokens):
            if following_index == index or following_index in consumed:
                continue
            suffix = _normalise_key(following["text"])
            key = _normalise_key(token["text"])
            combined = token["text"] + " " + following["text"]
            configured_combination = _configured_header_role(combined)
            if "%" in key:
                # A complete CGST % label must not consume the adjacent
                # Amount column and become a tax-amount label.
                continue
            tax_inclusive_rate = key == "RATE" and "INCL" in suffix and "TAX" in suffix
            if suffix not in suffixes and not tax_inclusive_rate and not configured_combination:
                continue
            if suffix == "PRICE" and key != "UNIT":
                continue
            if suffix == "RATE" and key not in ("UNIT", "BASE", "NET"):
                continue
            if suffix in ("NAME", "பெயர்") and key not in ("PRODUCT", "ITEM", "பொருள்", "பொருட்கள்"):
                continue
            horizontal = (
                following["x0"] >= token["x0"]
                and -median_height * 0.2 <= following["x0"] - token["x1"] <= median_height * 1.5
                and abs(following["yc"] - token["yc"]) <= median_height * 0.6
            )
            vertical = (
                0 < following["yc"] - token["yc"] <= median_height * (2.5 if key == "CD" and suffix in ("AMT", "AMOUNT") else 1.6)
                and abs(following["xc"] - token["xc"]) <= median_height
            )
            if not (horizontal or vertical) or not _table_header_role(combined):
                continue
            distance = abs(following["xc"] - token["xc"]) + abs(following["yc"] - token["yc"])
            if match is None or distance < match[0]:
                match = (distance, following_index, following, combined)
        if match:
            _distance, following_index, following, combined = match
            consumed.add(following_index)
            x0, x1 = min(token["x0"], following["x0"]), max(token["x1"], following["x1"])
            y0, y1 = min(token["y0"], following["y0"]), max(token["y1"], following["y1"])
            merged.append({**token, "text": combined, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "xc": (x0 + x1) / 2, "yc": (y0 + y1) / 2})
        else:
            merged.append(token)
        consumed.add(index)
    return merged


def _find_table_header(tokens):
    candidates = []
    median_height = _median_token_height(tokens)
    # OCR commonly returns configured multi-word headings (``Item Name``,
    # ``Taxable Amt``) as separate words. Join them before looking for the
    # mandatory description heading; joining only after finding it makes the
    # configured alias impossible to discover.
    searchable_tokens = _join_table_header_tokens(tokens, median_height)
    configured_rows = max(
        1, min(int((_OCR_TEMPLATE.get() or {}).get("data_rows_per_item") or 1), 3)
    )
    for token in searchable_tokens:
        if _table_header_role(token["text"]) == "description":
            nearby = [
                item
                for item in searchable_tokens
                if abs(item["yc"] - token["yc"]) <= (
                    median_height * (1.75 + (configured_rows - 1) * 1.15)
                    + (abs(item["xc"] - token["xc"]) * .08 if _is_table_header_label(item) else 0)
                )
                # A slanted header needs the horizontal allowance above, but
                # CASE/PCS in the first data row are values, not labels.
                and not (
                    item["yc"] - token["yc"] > median_height * 2.0
                    and _table_header_role(item["text"]) in {"case", "quantity", "free_quantity"}
                )
            ]
            nearby = _join_table_header_tokens(nearby, median_height)
            nearby_roles = {_table_header_role(item["text"]) for item in nearby}
            score = len(nearby_roles - {None, "description"})
            if score < 2:
                continue
            candidates.append((score, token, nearby))
    if not candidates:
        return None
    _score, product_token, nearby = max(candidates, key=lambda item: item[0])
    return product_token, nearby, median_height


def _is_table_header_label(token):
    return _table_header_role(token["text"]) is not None


def _table_header_slope(header_tokens, page_width):
    """Estimate the grid slope from labels, excluding overlapping data rows."""
    points = [
        (token["xc"], token["yc"])
        for token in header_tokens
        if _is_table_header_label(token)
    ]
    slopes = []
    for index, first in enumerate(points):
        for second in points[index + 1 :]:
            dx = second[0] - first[0]
            if abs(dx) < page_width * 0.08:
                continue
            slope = (second[1] - first[1]) / dx
            if abs(slope) <= 0.15:
                slopes.append(slope)
    return _median(slopes, 0.0)


def _column_centres(header_tokens, table_left, table_right):
    roles = [
        "serial",
        "hsn",
        "description",
        "upc",
        "mrp",
        "case",
        "quantity",
        "rate",
        "scheme_discount",
        "cash_discount",
        "taxable",
        "gst",
        "cgst",
        "sgst",
        "net",
    ]
    ratios = [0.025, 0.085, 0.205, 0.300, 0.355, 0.405, 0.450, 0.505, 0.565, 0.620, 0.685, 0.745, 0.805, 0.870, 0.965]
    width = max(table_right - table_left, 1.0)
    centres = {role: table_left + width * ratio for role, ratio in zip(roles, ratios)}

    detected = {}
    for token in header_tokens:
        role = _table_header_role(token["text"])
        # AMT on a second header line belongs to Taxable/CGST/SGST and is
        # resolved below; it must not overwrite the dense table's Net column.
        if _normalise_key(token["text"]) in ("AMT", "AMOUNT", "தொகை"):
            continue
        if role in roles or role == "barcode":
            detected.setdefault(role, []).append(token["xc"])
    for role, positions in detected.items():
        centres[role] = sum(positions) / len(positions)
    if "gst" in detected:
        tax_amount_headers = sorted(
            token["xc"]
            for token in header_tokens
            if _normalise_key(token["text"]) in ("AMT", "AMOUNT")
            and token["xc"] > centres["gst"]
        )
        if tax_amount_headers:
            centres["cgst"] = tax_amount_headers[0]
            detected["cgst"] = [tax_amount_headers[0]]
        if len(tax_amount_headers) > 1:
            centres["sgst"] = tax_amount_headers[1]
            detected["sgst"] = [tax_amount_headers[1]]
    if "gst" not in detected and "taxable" in detected and "cgst" in detected:
        centres["gst"] = (centres["taxable"] + centres["cgst"]) / 2
    if "net" not in detected and "sgst" in detected:
        preceding_gap = centres["sgst"] - centres.get("cgst", centres["gst"])
        centres["net"] = centres["sgst"] + max(preceding_gap, width * 0.035)

    ordered = sorted(
        ((role, centre) for role, centre in centres.items() if role != "barcode"),
        key=lambda item: roles.index(item[0]),
    )
    # Ignore a misleading OCR header match if it breaks the visual column order.
    previous = table_left
    for role, centre in ordered:
        if centre <= previous + width * 0.012:
            centres[role] = table_left + width * ratios[roles.index(role)]
        previous = centres[role]
    return roles, centres, detected


def _assign_columns(
    tokens,
    roles,
    centres,
    x_scale=1.0,
    pivot_x=0.0,
    source_anchor_x=None,
    target_anchor_x=None,
):
    ordered_centres = [centres[role] for role in roles]
    boundaries = [
        (ordered_centres[index] + ordered_centres[index + 1]) / 2
        for index in range(len(ordered_centres) - 1)
    ]
    columns = {role: [] for role in roles}
    for token in tokens:
        index = 0
        if source_anchor_x is not None and target_anchor_x is not None:
            adjusted_x = target_anchor_x + (
                token["xc"] - source_anchor_x
            ) / x_scale
        else:
            adjusted_x = pivot_x + (token["xc"] - pivot_x) / x_scale
        while index < len(boundaries) and adjusted_x >= boundaries[index]:
            index += 1
        columns[roles[index]].append(token)
    return columns, boundaries


def _description(tokens):
    if not tokens:
        return ""
    lines = _group_lines(tokens)
    parts = [line["text"] for line in lines if line["text"]]
    text = _normalise_space(" ".join(parts))
    text = re.sub(r"^PRODUCT\)?\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\s+(?:OFF|OFE)\s*\(?\s*WITH\s+FREE.*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s+(?:OFF|OFE)\s+FREE\b.*$", "", text, flags=re.IGNORECASE
    )
    text = re.sub(r"\s+(?:OFF|OFE)$", "", text, flags=re.IGNORECASE)
    # Stock/location notes are printed below the item name on Tally invoices;
    # they identify inventory placement, not the sellable product.
    text = re.sub(r"\s+LOCATION\s*:?\s*MAIN\s+LOCATION.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+BATCH\s*:?\s*.*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[B8](?=ROSES\b)", "3", text, flags=re.IGNORECASE)
    return text.strip(" -/|")


def _code_digits(text):
    groups = re.findall(r"\d+", text or "")
    candidates = [group for group in groups if 4 <= len(group) <= 8]
    if candidates:
        return max(candidates, key=len)
    joined = "".join(groups)
    if 9 <= len(joined) <= 10:
        return joined[-8:]
    return None


def _hsn_code(tokens):
    for token in sorted(tokens, key=lambda item: (item["yc"], item["xc"])):
        digits = _code_digits(token["text"])
        if digits:
            return digits
    return None


def _labelled_column_number(tokens):
    """Read an explicit number, retaining the distinction between absent and 0."""
    text = _normalise_space(" ".join(token["text"] for token in sorted(tokens, key=lambda item: item["xc"])))
    text = re.sub(r"^(?:₹|RS\.?|ரூ\.?)\s*", "", text, flags=re.IGNORECASE)
    # If OCR misses the narrow `per` header, its CASE/PCS value may be
    # assigned to the adjacent Rate column. Treat it as a harmless unit.
    if not re.fullmatch(
        r"\d[\d,]*(?:\.\d+)?\s*%?\s*(?:PCS?|PIECES?|CASES?|NOS?|KG|G|LTRS?|LIT(?:RE|ER)S?)?",
        text,
        re.IGNORECASE,
    ):
        return None
    return _parse_float(text)


def _extract_stacked_table_lines(tokens, page_width, header, rows_per_item):
    """Parse tables where each product uses aligned upper/lower value rows.

    The selected vendor template supplies both the row count and aliases. The
    header's vertical levels define which semantic columns apply to each value
    row, allowing layouts such as MRP/CGST% and Gross Amount/SGST% without a
    vendor-specific parser.
    """
    product_token, header_tokens, median_height = header
    labelled = [token for token in header_tokens if _is_table_header_label(token)]
    primary_role_names = {
        "serial", "description", "uom", "barcode", "upc", "mrp", "case",
        "quantity", "free_quantity", "rate", "tax_inclusive_rate",
        "scheme_discount", "cash_discount", "discount_percent",
        "discount_amount", "other_discount", "gross_amount", "net",
    }
    secondary_role_names = {
        "hsn", "secondary_quantity", "taxable", "gst", "cgst_percent",
        "sgst_percent", "igst_percent", "cgst_amount", "sgst_amount",
        "igst_amount", "tax_amount",
    }
    # Estimate perspective from headings that belong to the same upper row.
    # Mixing CGST/HSN headings from the lower row flattens the slope and then
    # splits a photographed product name from its numeric values.
    slope_headers = [
        token for token in labelled
        if _table_header_role(token["text"]) in {"description", "uom", "mrp", "rate"}
    ]
    slope = _table_header_slope(slope_headers or labelled, page_width)

    def adjusted_y(token):
        return token["yc"] - slope * (token["xc"] - product_token["xc"])

    adjusted_headers = []
    for token in labelled:
        y = adjusted_y(token)
        adjusted_headers.append({
            **token,
            "yc": y,
            "y0": y - (token["y1"] - token["y0"]) / 2,
            "y1": y + (token["y1"] - token["y0"]) / 2,
        })
    row_layouts = []
    if rows_per_item == 2:
        # A heading such as Item Name can span both printed header rows, so
        # visual line clustering alone may put it on a third middle line.
        # The configured semantic role tells us which value row owns it.
        primary_reference_y = _median([
            token["yc"] for token in adjusted_headers
            if _table_header_role(token["text"]) in {
                "uom", "mrp", "rate", "free_quantity", "discount_percent",
            }
        ], product_token["yc"])
        for layout_number, allowed_roles in enumerate((primary_role_names, secondary_role_names)):
            positioned = {}
            for token in adjusted_headers:
                role = _table_header_role(token["text"])
                if role in allowed_roles:
                    positioned.setdefault(role, []).append(token)
            centres = {
                role: (
                    min(values, key=lambda item: abs(item["yc"] - primary_reference_y))["xc"]
                    if layout_number == 0 else
                    sum(item["xc"] for item in values) / len(values)
                )
                for role, values in positioned.items()
            }
            row_layouts.append((sorted(centres, key=centres.get), centres))
    else:
        header_levels = _group_lines(adjusted_headers)
        if len(header_levels) < rows_per_item:
            return [], [
                "The template expects %s printed rows per product, but the same number of header rows was not detected."
                % rows_per_item
            ]
        # Keep the lowest N header lines nearest the product data. A title or
        # unrelated label above the table cannot shift the configured row mapping.
        header_levels = header_levels[-rows_per_item:]
        for level in header_levels:
            positions = {}
            for token in level["tokens"]:
                role = _table_header_role(token["text"])
                if role:
                    positions.setdefault(role, []).append(token["xc"])
            centres = {
                role: sum(values) / len(values)
                for role, values in positions.items()
            }
            row_layouts.append((sorted(centres, key=centres.get), centres))

    primary_roles, primary_centres = row_layouts[0]
    if not {"description", "quantity", "rate"}.issubset(primary_centres):
        return [], [
            "The first stacked header row must map Product Description, Quantity, and Purchase Rate."
        ]

    header_bottom = max(adjusted_y(token) for token in labelled) + median_height * 0.55
    summary_keys = {
        "TOTAL", "GRANDTOTAL", "SUBTOTAL", "NETAMTPAYABLE", "TOTALTAXAMT",
    }
    summary_y = min((
        adjusted_y(token) - median_height * 0.5
        for token in tokens
        if adjusted_y(token) > header_bottom + median_height
        and _normalise_key(token["text"]).rstrip(":") in summary_keys
    ), default=float("inf"))
    body = []
    for token in tokens:
        y = adjusted_y(token)
        if header_bottom < y < summary_y:
            offset = y - token["yc"]
            body.append({
                **token,
                "yc": y,
                "y0": token["y0"] + offset,
                "y1": token["y1"] + offset,
            })

    description_x = primary_centres["description"]
    serial_candidates = sorted([
        token for token in body
        if re.fullmatch(r"\d{1,3}", token["text"].strip())
        and token["xc"] < description_x - median_height * 0.4
    ], key=lambda token: token["yc"])
    anchors = []
    for token in serial_candidates:
        number = int(token["text"])
        if number <= 0 or number > 999:
            continue
        if anchors and abs(token["yc"] - anchors[-1]["yc"]) < median_height * 0.7:
            continue
        anchors.append(token)

    # Serial digits are often lost against a vertical grid rule. In layouts
    # with an explicit UOM column, values such as PAC/PCS/CASE are a more
    # reliable upper-row anchor and remain entirely template-driven.
    uom_x = primary_centres.get("uom")
    if uom_x is not None:
        uom_anchors = sorted([
            token for token in body
            if re.fullmatch(
                r"(?:[A-Z]{2,5}|PCS?|PIECES?|CASE|NOS?|KG|G|LTRS?|LIT(?:RE|ER)S?)\.?,?",
                token["text"].strip(), re.IGNORECASE,
            )
            and abs(token["xc"] - uom_x) <= median_height * 5
        ], key=lambda token: token["yc"])
        serial_anchors = anchors
        anchors = list(uom_anchors)
        for token in serial_anchors:
            if not any(
                abs(token["yc"] - uom_anchor["yc"]) < median_height * 0.9
                for uom_anchor in uom_anchors
            ):
                anchors.append(token)
        anchors.sort(key=lambda token: token["yc"])

    # Keep the two printed value rows separate. The general line grouper uses
    # a wider tolerance for prose and can merge adjacent 2-row table cells.
    stacked_tolerance = max(3.0, median_height * 0.38)
    grouped_body = []
    for token in sorted(body, key=lambda item: (item["yc"], item["xc"])):
        nearby = [
            line for line in grouped_body[-8:]
            if abs(line["yc"] - token["yc"]) <= stacked_tolerance
        ]
        line = min(nearby, key=lambda item: abs(item["yc"] - token["yc"])) if nearby else None
        if line is None:
            grouped_body.append({"yc": token["yc"], "tokens": [token]})
        else:
            line["tokens"].append(token)
            line["yc"] = sum(item["yc"] for item in line["tokens"]) / len(line["tokens"])
    for line in grouped_body:
        line["tokens"].sort(key=lambda item: item["xc"])
        line["text"] = _normalise_space(" ".join(item["text"] for item in line["tokens"]))
    if not anchors:
        # Fallback for cropped photos without the serial column: any line that
        # satisfies all required first-row fields can anchor an item block.
        for line in grouped_body:
            columns, _boundaries = _assign_columns(
                line["tokens"], primary_roles, primary_centres
            )
            if (
                _description(columns.get("description", []))
                and _labelled_column_number(columns.get("quantity", [])) is not None
                and _labelled_column_number(columns.get("rate", [])) is not None
            ):
                anchors.append({"yc": line["yc"], "text": str(len(anchors) + 1)})
    if not anchors:
        return [], ["Product rows were not detected below the stacked table header."]

    quantity_pattern = re.compile(r"\d+(?:\.\d+)?")
    extracted = []
    warnings = []
    for index, anchor in enumerate(anchors):
        previous_y = anchors[index - 1]["yc"] if index else header_bottom
        next_y = anchors[index + 1]["yc"] if index + 1 < len(anchors) else summary_y
        lower = (previous_y + anchor["yc"]) / 2 if index else header_bottom
        upper = (anchor["yc"] + next_y) / 2 if next_y != float("inf") else anchor["yc"] + median_height * (rows_per_item + 1.2)
        block_lines = [line for line in grouped_body if lower < line["yc"] < upper]
        if not block_lines:
            continue
        # OCR may split one sloping printed row into two or three visual
        # fragments. Select the smallest fragment combination that satisfies
        # the configured description/quantity/rate columns.
        primary_choice = None
        all_block_tokens = sorted(
            (token for line in block_lines for token in line["tokens"]),
            key=lambda token: token["xc"],
        )
        block_columns, _boundaries = _assign_columns(
            all_block_tokens, primary_roles, primary_centres
        )
        closest_tokens = []
        for role, column_tokens in block_columns.items():
            if role == "description":
                closest_tokens.extend([
                    token for token in column_tokens
                    if any(character.isalpha() for character in token["text"])
                    and not re.fullmatch(
                        r"\|?\d+\.\d+(?:KG|G|LTRS?|LIT(?:RE|ER)S?)",
                        token["text"].strip(), re.IGNORECASE,
                    )
                    and not re.fullmatch(r"\d{7,8}", token["text"].strip())
                    and abs(token["yc"] - anchor["yc"]) <= median_height * 0.9
                ])
            elif role in {"quantity", "rate", "free_quantity", "discount_percent"}:
                numeric = [
                    token for token in column_tokens
                    if _labelled_column_number([token]) is not None
                ]
                if numeric:
                    closest_tokens.append(
                        min(numeric, key=lambda token: abs(token["yc"] - anchor["yc"]))
                    )
        closest_tokens.sort(key=lambda token: token["xc"])
        closest_columns, _boundaries = _assign_columns(
            closest_tokens, primary_roles, primary_centres
        )
        closest_description = _description(closest_columns.get("description", []))
        if (
            closest_description
            and any(character.isalpha() for character in closest_description)
            and _labelled_column_number(closest_columns.get("quantity", [])) is not None
            and _labelled_column_number(closest_columns.get("rate", [])) is not None
        ):
            primary_choice = (1000000.0, tuple(), closest_tokens)
        for size in range(1, min(3, len(block_lines)) + 1):
            for fragment_indexes in itertools.combinations(range(len(block_lines)), size):
                selected = [block_lines[position] for position in fragment_indexes]
                selected_tokens = sorted(
                    (token for line in selected for token in line["tokens"]),
                    key=lambda token: token["xc"],
                )
                columns, _boundaries = _assign_columns(
                    selected_tokens, primary_roles, primary_centres
                )
                description_value = _description(columns.get("description", []))
                quantity_value = _labelled_column_number(columns.get("quantity", []))
                rate_value = _labelled_column_number(columns.get("rate", []))
                if not (
                    description_value
                    and any(character.isalpha() for character in description_value)
                    and quantity_value is not None
                    and rate_value is not None
                ):
                    continue
                span = max(line["yc"] for line in selected) - min(line["yc"] for line in selected)
                contaminated = sum(
                    bool(re.fullmatch(
                        r"\|?\d+\.\d+(?:KG|G|LTRS?|LIT(?:RE|ER)S?)", token["text"].strip(),
                        re.IGNORECASE,
                    )) or bool(re.fullmatch(r"\d{7,8}", token["text"].strip()))
                    for token in columns.get("description", [])
                )
                score = (
                    len(description_value) * 2 - size * 3
                    - span / max(median_height, 1) - contaminated * 100
                )
                candidate = (score, fragment_indexes, selected_tokens)
                if primary_choice is None or candidate[0] > primary_choice[0]:
                    primary_choice = candidate
        if primary_choice is None:
            warnings.append(
                "Skipped stacked product row %s because product, quantity, or rate was unclear."
                % (index + 1)
            )
            continue
        _score, primary_indexes, primary_tokens = primary_choice
        primary_line = {
            "yc": anchor["yc"], "tokens": primary_tokens,
            "text": _normalise_space(" ".join(token["text"] for token in primary_tokens)),
        }

        remaining_lines = [
            line for position, line in enumerate(block_lines)
            if position not in primary_indexes
        ]
        secondary_line = {"yc": anchor["yc"] + median_height, "tokens": [], "text": ""}
        if remaining_lines:
            def secondary_score(line):
                columns, _boundaries = _assign_columns(
                    line["tokens"], row_layouts[1][0], row_layouts[1][1]
                )
                return sum(
                    _labelled_column_number(columns.get(role, [])) is not None
                    for role in ("taxable", "cgst_percent", "sgst_percent", "igst_percent")
                ) + bool(_hsn_code(columns.get("hsn", [])))
            secondary_line = max(remaining_lines, key=secondary_score)
        value_lines = [primary_line, secondary_line]

        row_columns = []
        for row_number, value_line in enumerate(value_lines):
            roles, centres = row_layouts[row_number]
            columns, _boundaries = _assign_columns(value_line["tokens"], roles, centres)
            row_columns.append(columns)
        primary = row_columns[0]

        # A wide, left-aligned product name can cross the midpoint between its
        # header and UOM. Use the next physical header as its actual right edge.
        right_centres = sorted(
            centre for role, centre in primary_centres.items()
            if centre > description_x and role != "serial"
        )
        description_right = (
            (description_x + right_centres[0]) / 2
            if right_centres else page_width
        )
        description_tokens = [
            token for token in primary_line["tokens"]
            if token["x0"] >= product_token["x0"] - median_height * 0.3
            and token["x0"] < description_right
            and not (
                token["text"].strip().isdigit()
                and token["xc"] < description_x - median_height * 0.4
            )
        ]
        description = _description(description_tokens) or _description(primary.get("description", []))
        description = re.sub(
            r"(?:\s*\|?\s*\d+\.\d+(?:KG|G|LTRS?|LIT(?:RE|ER)S?))+$",
            "", description or "", flags=re.IGNORECASE,
        ).strip(" .|-")
        quantity = _labelled_column_number(primary.get("quantity", []))
        rate = _labelled_column_number(primary.get("rate", []))
        if (
            not description
            or not any(character.isalpha() for character in description)
            or quantity is None
            or rate is None
            or not quantity_pattern.fullmatch(str(quantity))
        ):
            warnings.append(
                "Skipped stacked product row %s because product, quantity, or rate was unclear."
                % (index + 1)
            )
            continue
        free_quantity = _labelled_column_number(primary.get("free_quantity", [])) or 0.0
        if abs(free_quantity - round(free_quantity)) > 0.001:
            # A fractional value in this physical column is normally the
            # SGST amount from the lower row, not a free-pack quantity.
            free_quantity = 0.0
        if quantity + free_quantity <= 0:
            continue

        secondary_columns = row_columns[1:]
        hsn_tokens = [
            token
            for columns in secondary_columns
            for token in columns.get("hsn", [])
        ]
        central = next((
            value for value in (
                _labelled_column_number(columns.get("cgst_percent", []))
                for columns in secondary_columns
            ) if value is not None
        ), None)
        state = next((
            value for value in (
                _labelled_column_number(columns.get("sgst_percent", []))
                for columns in secondary_columns
            ) if value is not None
        ), None)
        integrated = next((
            value for value in (
                _labelled_column_number(columns.get("igst_percent", []))
                for columns in secondary_columns
            ) if value is not None
        ), None)
        gst = integrated if integrated is not None else (
            central + state if central is not None and state is not None else 0.0
        )
        if not 0 <= gst <= 100:
            gst = 0.0
            warnings.append("Tax percentage was unclear for stacked product row %s." % (index + 1))

        discount = _labelled_column_number(primary.get("discount_percent", []))
        discount_amount = _labelled_column_number(primary.get("discount_amount", []))
        gross = quantity * rate
        if (discount is None or discount == 0) and discount_amount and gross:
            discount = discount_amount / gross * 100
        if discount is None or not 0 <= discount <= 100:
            discount = 0.0
        taxable = next((
            value for value in (
                _labelled_column_number(columns.get("taxable", []))
                for columns in secondary_columns
            ) if value is not None
        ), None)
        calculated = gross * (1 - discount / 100)
        if taxable is not None and abs(calculated - taxable) > max(0.15, taxable * 0.015):
            warnings.append(
                "Quantity, rate, discount, and taxable amount disagree for stacked product row %s."
                % (index + 1)
            )

        block_tokens = [token for line in value_lines for token in line["tokens"]]
        score = sum(token["score"] for token in block_tokens) / max(len(block_tokens), 1)
        extracted.append({
            "description": description,
            "vendor_product_code": None,
            "barcode": None,
            "hsn_code": _hsn_code(hsn_tokens),
            "mrp": max(_labelled_column_number(primary.get("mrp", [])) or 0.0, 0.0),
            "quantity": quantity,
            "free_quantity": free_quantity,
            "purchase_rate": rate,
            "discount_percent": round(discount, 4),
            "gst_percent": gst,
            "confidence": round(score * 100, 2),
        })
    known_gst = sorted({
        line["gst_percent"] for line in extracted if line["gst_percent"] > 0
    })
    if len(known_gst) == 1:
        for line in extracted:
            if line["gst_percent"] == 0:
                line["gst_percent"] = known_gst[0]
                warnings.append(
                    "Applied the invoice's repeated GST rate to a stacked row whose tax cells were unclear."
                )
    return extracted, warnings


def _group_rate_first_rows(tokens, centres, median_height):
    """Anchor perspective-distorted simple tables to both printed money columns."""
    money = re.compile(r"\d[\d,]*\.\d{2}")
    rate_tokens = sorted([t for t in tokens if money.fullmatch(t["text"])
                          and abs(t["xc"] - centres["rate"]) < median_height * 2], key=lambda t: t["yc"])
    amount_tokens = sorted([t for t in tokens if money.fullmatch(t["text"])
                            and abs(t["xc"] - centres["net"]) < median_height * 2], key=lambda t: t["yc"])
    if not rate_tokens or len(rate_tokens) != len(amount_tokens):
        return _group_lines(tokens)
    pairs = list(zip(rate_tokens, amount_tokens))
    if any(abs(left["yc"] - right["yc"]) > median_height * 1.5 for left, right in pairs):
        return _group_lines(tokens)
    rows = [{"yc": left["yc"], "tokens": []} for left, _ in pairs]
    for token in tokens:
        distances = []
        for left, right in pairs:
            fraction = (token["xc"] - left["xc"]) / max(right["xc"] - left["xc"], 1)
            expected_y = left["yc"] + fraction * (right["yc"] - left["yc"])
            distances.append(abs(token["yc"] - expected_y))
        index = min(range(len(distances)), key=distances.__getitem__)
        if distances[index] <= median_height * .7:
            rows[index]["tokens"].append(token)
    return rows


def _extract_labelled_table_lines(tokens, page_width, header):
    """Parse simple Tamil/English bills using only columns actually labelled.

    The SNK case/UPC layout below has its own geometric recovery. A small
    receipt with only Product, Quantity, Rate and Amount must not inherit
    those extra columns or treat an amount as a tax percentage.
    """
    product_token, header_tokens, median_height = header
    labelled = [token for token in header_tokens if _is_table_header_label(token)]
    positions = {}
    for token in labelled:
        role = _table_header_role(token["text"])
        positions.setdefault(role, []).append(token["xc"])
    if not {"description", "quantity", "rate"}.issubset(positions):
        return [], ["Product, quantity and unit-price columns must be readable to extract this bill safely."]
    centres = {role: sum(values) / len(values) for role, values in positions.items()}
    roles = sorted(centres, key=centres.get)
    slope = _table_header_slope(labelled, page_width)
    rate_first = (
        centres["rate"] < centres["description"] < centres["quantity"]
        and "net" in centres and centres["net"] > centres["quantity"]
        and set(roles).issubset({"serial", "rate", "description", "quantity", "net"})
    )

    # A Tally-style bill commonly prints tax once in the footer instead of in
    # every product row. Preserve that declared rate for all extracted rows.
    page_text = " ".join(line["text"] for line in _group_lines(tokens))
    cgst_rates = re.findall(r"\bCGST\b\s*@?\s*(\d+(?:\.\d+)?)\s*%", page_text, re.IGNORECASE)
    sgst_rates = re.findall(r"\bSGST\b\s*@?\s*(\d+(?:\.\d+)?)\s*%", page_text, re.IGNORECASE)
    global_gst = None
    if cgst_rates and sgst_rates:
        global_gst = _parse_float(cgst_rates[0]) + _parse_float(sgst_rates[0])
        if not 0 <= global_gst <= 100:
            global_gst = None

    def adjusted_y(token):
        return token["yc"] - slope * (token["xc"] - product_token["xc"])

    header_bottom = max(adjusted_y(token) for token in labelled) + median_height * 0.45
    summary_keys = {"TOTAL", "GRANDTOTAL", "SUBTOTAL", "மொத்தம்", "மொத்ததொகை", "கூடுதல்", "நிகரதொகை"}
    summary_y = min(
        (
            adjusted_y(token) - median_height * 0.5
            for token in tokens
            if adjusted_y(token) > header_bottom + median_height
            and token["xc"] < centres["quantity"]
            and _normalise_key(token["text"]) in summary_keys
        ),
        default=float("inf"),
    )
    # Footer tax breakdowns are another table, not purchased products. Their
    # labels may be on the right rather than underneath the description.
    footer_y = min((
        adjusted_y(token) - median_height * .5
        for token in tokens
        if adjusted_y(token) > header_bottom + median_height
        and _normalise_key(token["text"]).rstrip(":") in {
            "GSTHEAD", "TITEMS", "TOTALITEMS", "TOTALGST", "GOODSVALUE", "GRANDTOTAL",
        }
        or adjusted_y(token) > header_bottom + median_height
        and _normalise_key(token["text"]).startswith(("OUTPUTCGST", "OUTPUTSGST", "DISCOUNTALLOWED"))
    ), default=float("inf"))
    summary_y = min(summary_y, footer_y)
    body = []
    for token in tokens:
        y = adjusted_y(token)
        if header_bottom < y < summary_y:
            if _normalise_key(token["text"]).startswith(("LOCATION", "BATCH")):
                continue
            offset = y - token["yc"]
            body.append({**token, "yc": y, "y0": token["y0"] + offset, "y1": token["y1"] + offset})

    if "serial" not in centres:
        # OCR can read the narrow Sl header as '8'. Infer that column only
        # from a separate, aligned, consecutive row-number sequence.
        serials = sorted([
            token for token in body
            if token["text"].isdigit() and 0 < int(token["text"]) < 500
            and token["x1"] < product_token["x0"] - median_height * .35
        ], key=lambda token: token["yc"])
        if (len(serials) >= 2
                and all(int(second["text"]) == int(first["text"]) + 1
                        for first, second in zip(serials, serials[1:]))
                and max(t["xc"] for t in serials) - min(t["xc"] for t in serials) < median_height):
            centres["serial"] = sum(t["xc"] for t in serials) / len(serials)
            roles = sorted(centres, key=centres.get)

    extracted = []
    warnings = []
    last_row_y = None
    pending_numeric_row = None
    quantity_pattern = re.compile(
        r"(\d+(?:\.\d+)?)(?:\s*\+\s*(\d+(?:\.\d+)?))?"
        r"\s*(?:PCS?|PIECES?|CASES?|BAGS?|NOS?|KG|G|LTRS?|LIT(?:RE|ER)S?)?",
        re.IGNORECASE,
    )

    def quantity_match_for(column_tokens, row_y):
        text = _normalise_space(" ".join(token["text"] for token in column_tokens))
        match = quantity_pattern.fullmatch(text)
        if match:
            return text, match
        # A repeated billed/ordered quantity can share a geometric row after
        # perspective correction. Prefer the token nearest the product name.
        for item in sorted(column_tokens, key=lambda token: abs(token["yc"] - row_y)):
            match = quantity_pattern.fullmatch(_normalise_space(item["text"]))
            if match:
                return _normalise_space(item["text"]), match
        return text, None

    if rate_first:
        # Exclude background text beyond the bill's labelled amount column.
        right_edge = max(t["x1"] for t in labelled if _table_header_role(t["text"]) == "net")
        body = [t for t in body if t["xc"] <= right_edge + median_height]
    grouped_rows = _group_rate_first_rows(body, centres, median_height) if rate_first else _group_lines(body)
    for index, row in enumerate(grouped_rows, start=1):
        columns, _boundaries = _assign_columns(row["tokens"], roles, centres)
        if rate_first:
            # A centred Description heading can sit far right of short names.
            # Alphabetic tokens following the rate belong to the name column.
            rate_right = max(t["x1"] for t in labelled if _table_header_role(t["text"]) == "rate")
            names = [t for t in columns["rate"] if t["x0"] > rate_right
                     and any(c.isalpha() for c in t["text"])]
            columns["description"].extend(names)
            columns["rate"] = [t for t in columns["rate"] if t not in names]
            # Separate handwritten prices/check marks are not product names.
            names = sorted(columns["description"], key=lambda t: t["x0"])
            printed_names = []
            for token in names:
                if printed_names and token["x0"] - printed_names[-1]["x1"] > median_height * 1.5:
                    warnings.append("Separate marks beside product %s were omitted from its name; review the description." % index)
                    break
                printed_names.append(token)
            columns["description"] = printed_names
        if "serial" in columns:
            # The centre of a wide Product Name header lies far to the right
            # of left-aligned names. Do not put a brand like BRU in Sl merely
            # because it lies left of the midpoint between the header centres.
            name_start = product_token["x0"] - median_height * .25
            names = [token for token in columns["serial"] if token["x0"] >= name_start]
            columns["description"].extend(names)
            columns["serial"] = [token for token in columns["serial"] if token not in names]
        description = _description(columns["description"])
        quantity_text, quantity_match = quantity_match_for(columns["quantity"], row["yc"])
        rate = _labelled_column_number(columns["rate"])
        if rate_first and rate is not None and rate > 0:
            amount = _labelled_column_number(columns["net"])
            unit_text = " ".join(t["text"] for t in columns["quantity"])
            unit = re.search(r"\b(BAGS?|KG)\b", unit_text, re.IGNORECASE)
            # A pen stroke can obscure a printed quantity (1 -> 11, 10 -> T0).
            # Recover only on a simple, undiscounted layout with a printed unit
            # and an exact, positive quantity supported by rate and line amount.
            if unit and amount is not None and amount > 0 and (
                not quantity_match or abs(float(quantity_match[1]) * rate - amount) > .02
            ):
                recovered = round(amount / rate, 3)
                if quantity_match:
                    # Only repair a recognisable duplicated digit from a pen
                    # stroke (1 -> 11). Other explicit quantity conflicts may
                    # represent an unlabelled discount and must not be guessed.
                    printed = quantity_match[1]
                    digit = str(int(recovered)) if recovered.is_integer() else ""
                    duplicated_digit = (
                        len(digit) == 1 and len(printed) > 1
                        and printed == digit * len(printed)
                        and not quantity_match[2]
                    )
                    if not duplicated_digit:
                        warnings.append("Skipped OCR row %s: its explicit quantity disagrees with the printed amount." % index)
                        continue
                if (0 < recovered <= 10000 and abs(recovered * rate - amount) <= .02
                        and (unit[1].upper() == "KG" or recovered.is_integer())):
                    quantity_text = str(recovered)
                    quantity_match = quantity_pattern.fullmatch(quantity_text)
                    warnings.append("Quantity for row %s was recovered from its printed rate and amount because the quantity is obscured; verify it before import." % index)
        if (
            description
            and (not quantity_match or rate is None)
            and pending_numeric_row
            and row["yc"] - pending_numeric_row["yc"] <= median_height * 1.25
        ):
            columns, _boundaries = _assign_columns(
                pending_numeric_row["tokens"] + row["tokens"], roles, centres
            )
            description = _description(columns["description"])
            quantity_text, quantity_match = quantity_match_for(columns["quantity"], row["yc"])
            rate = _labelled_column_number(columns["rate"])
        if not description and (quantity_text or rate is not None):
            pending_numeric_row = row
            continue
        pending_numeric_row = None
        if re.match(r"^(?:LOCATION|BATCH)\b", description, re.IGNORECASE):
            continue
        if description and not quantity_text and rate is None:
            # Keep a wrapped name immediately following its numeric row.
            if extracted and last_row_y is not None and row["yc"] - last_row_y <= median_height * 1.6 and not columns.get("serial"):
                extracted[-1]["description"] = _normalise_space(extracted[-1]["description"] + " " + description)
                last_row_y = row["yc"]
            else:
                warnings.append("Skipped OCR row %s because its quantity or unit price was unclear." % index)
            continue
        if not description or not any(character.isalpha() for character in description) or not quantity_match or rate is None:
            if description or quantity_text:
                warnings.append("Skipped OCR row %s because its product name, quantity or unit price was unclear." % index)
            continue
        quantity = float(quantity_match.group(1))
        free_quantity = float(quantity_match.group(2) or 0)
        if "case" in columns:
            cases = _labelled_column_number(columns["case"])
            if cases:
                units_per_case = _labelled_column_number(columns.get("upc", []))
                if units_per_case is None or units_per_case <= 0:
                    warnings.append("Skipped OCR row %s: case quantity needs a readable units-per-case column." % index)
                    continue
                quantity += cases * units_per_case
        if "free_quantity" in columns:
            free_tokens = columns["free_quantity"]
            free_text = _normalise_space(" ".join(token["text"] for token in free_tokens))
            separate_free = _labelled_column_number(free_tokens)
            if separate_free is not None and separate_free >= 0:
                if free_quantity and abs(free_quantity - separate_free) > 0.0001:
                    warnings.append("Conflicting free quantities for OCR row %s; review before import." % index)
                else:
                    free_quantity = separate_free
            elif free_text not in ("", "-", "—", "–"):
                warnings.append("Free quantity was unclear for OCR row %s; review before import." % index)
        if quantity + free_quantity <= 0:
            warnings.append("Skipped OCR row %s because its quantity was zero." % index)
            continue

        gst = _labelled_column_number(columns.get("gst", []))
        if gst is None:
            integrated = _labelled_column_number(columns.get("igst_percent", []))
            central = _labelled_column_number(columns.get("cgst_percent", []))
            state = _labelled_column_number(columns.get("sgst_percent", []))
            if integrated is not None:
                gst = integrated
            elif central is not None and state is not None:
                gst = central + state
        if gst is None:
            gst = global_gst
        if gst is None or not 0 <= gst <= 100:
            warnings.append("Tax percentage was not readable for OCR row %s; verify its tax before import." % index)
            gst = 0.0

        discount = _labelled_column_number(columns.get("discount_percent", []))
        discount_amounts = [
            _labelled_column_number(columns.get(role, []))
            for role in ("scheme_discount", "cash_discount")
            if role in columns
        ]
        if discount is None and discount_amounts and all(value is not None for value in discount_amounts) and quantity * rate:
            discount = sum(discount_amounts) / (quantity * rate) * 100
        if discount is not None and not 0 <= discount <= 100:
            warnings.append("Discount was unclear for OCR row %s; verify it before import." % index)
            discount = None
        explicit_taxable = _labelled_column_number(columns.get("taxable", []))
        printed_net = _labelled_column_number(columns.get("net", []))
        gross = quantity * rate * (1 - (discount or 0) / 100)
        # A lost decimal (0.600 -> 0600) must not become hundreds of units.
        # Do not guess the decimal position: reject grossly inconsistent rows.
        comparison = explicit_taxable if explicit_taxable is not None else printed_net
        if comparison is not None and gross > 0 and (comparison < gross * .1 or comparison > gross * 3):
            warnings.append("Skipped OCR row %s: quantity/price and printed amount differ greatly; check decimal points." % index)
            continue
        if explicit_taxable is not None:
            calculated = quantity * rate * (1 - (discount or 0) / 100)
            if abs(calculated - explicit_taxable) > max(0.1, explicit_taxable * 0.01):
                warnings.append("Quantity, unit price and taxable amount disagree for OCR row %s; review this row." % index)

        barcode = re.sub(r"\D", "", "".join(token["text"] for token in columns.get("barcode", [])))
        score = sum(token["score"] for token in row["tokens"]) / max(len(row["tokens"]), 1)
        extracted.append({
            "description": description,
            "vendor_product_code": None,
            "barcode": barcode if 8 <= len(barcode) <= 14 else None,
            "hsn_code": _hsn_code(columns.get("hsn", [])),
            "mrp": max(_labelled_column_number(columns.get("mrp", [])) or 0.0, 0.0),
            "quantity": quantity,
            "free_quantity": free_quantity,
            "purchase_rate": rate,
            "discount_percent": round(discount or 0.0, 4),
            "gst_percent": gst,
            "confidence": round(score * 100, 2),
        })
        last_row_y = row["yc"]
    return extracted, warnings


def _extract_box_piece_table_lines(tokens, header):
    """Read box-priced or piece-priced rows with grouped component-tax columns.

    This layout has no units-per-case column: a row is billed in either boxes
    or pieces. Only accept that interpretation when the printed amounts agree.
    """
    product, headings, median_height = header
    by_key = {_normalise_key(t["text"]): t for t in headings}
    required = ("BOX", "PCS", "RATE", "TRADEDIS", "TAXABLE")
    if not all(key in by_key for key in required):
        return None
    tax_parents = [t for t in headings if _normalise_key(t["text"]) in ("CGST", "SGSTUTGST", "SGST")]
    if len(tax_parents) != 2:
        return None
    centres = {
        "description": product["xc"], "rate": by_key["RATE"]["xc"],
        "case": by_key["BOX"]["xc"], "quantity": by_key["PCS"]["xc"],
        "discount": by_key["TRADEDIS"]["xc"], "taxable": by_key["TAXABLE"]["xc"],
    }
    # Use labels on the same printed baseline, not the upper tax group titles.
    baseline = [product] + [by_key[key] for key in ("RATE", "BOX", "PCS", "TRADEDIS")]
    slopes = [(b["yc"] - a["yc"]) / (b["xc"] - a["xc"])
              for a, b in itertools.combinations(baseline, 2) if b["xc"] - a["xc"] > median_height * 3]
    slope = _median(slopes, 0.0)
    def y(token):
        return token["yc"] - slope * (token["xc"] - product["xc"])
    children = [t for t in headings if _normalise_key(t["text"]) in ("RATE%", "AMT")]
    for parent in tax_parents:
        prefix = "cgst" if _normalise_key(parent["text"]) == "CGST" else "sgst"
        owned = [t for t in children if min(tax_parents, key=lambda p: abs(p["xc"] - t["xc"])) is parent]
        for key, suffix in (("RATE%", "percent"), ("AMT", "amount")):
            matches = [t for t in owned if _normalise_key(t["text"]) == key]
            if len(matches) != 1:
                return None
            centres[prefix + "_" + suffix] = matches[0]["xc"]
    net = by_key.get("LINEVALUE")
    if not net:
        return None
    centres["net"] = net["xc"]
    hsn = next((t for t in headings if _table_header_role(t["text"]) == "hsn"), None)
    if hsn:
        centres["hsn"] = hsn["xc"]
    serial = next((t for t in headings if _table_header_role(t["text"]) == "serial"), None)
    if not serial:
        return None
    centres["serial"] = serial["xc"]
    header_bottom = max(y(t) for t in baseline + children) + median_height * .6
    footer = min((y(t) for t in tokens if y(t) > header_bottom and
                  _normalise_key(t["text"]) in ("TOTAL", "GSTSUMMARY", "BASICVALUE")), default=float("inf"))
    body = [t for t in tokens if header_bottom < y(t) < footer - median_height * .5]
    anchors = sorted([t for t in body if re.fullmatch(r"\d{1,3}", t["text"])
                      and abs(t["xc"] - centres["serial"]) < median_height], key=y)
    if not anchors:
        return [], ["Product row numbers were not readable in the box/piece table."]
    roles = sorted(centres, key=centres.get)
    extracted, warnings = [], []
    for index, anchor in enumerate(anchors):
        row_y = y(anchor)
        lower = (y(anchors[index - 1]) + row_y) / 2 if index else header_bottom
        upper = (row_y + y(anchors[index + 1])) / 2 if index + 1 < len(anchors) else row_y + median_height
        row = [t for t in body if lower <= y(t) < upper]
        expanded = []
        def part(token, text, role):
            return {**token, "text": text, "xc": centres[role],
                    "x0": centres[role] - 1, "x1": centres[role] + 1}
        for token in row:
            text = token["text"]
            # Neural OCR may combine HSN, the complete name, and the rate.
            if token["x0"] < product["xc"] and token["x1"] > product["xc"]:
                if hsn and token["x0"] < (hsn["xc"] + product["xc"]) / 2:
                    match = re.match(r"^(\d{4,8})\s+(.+)$", text)
                    if match:
                        expanded.append(part(token, match[1], "hsn"))
                        text = match[2]
                if token["x1"] > centres["rate"]:
                    match = re.match(r"^(.+?)\s+(\d[\d,]*\.\d{2})$", text)
                    if match:
                        expanded.append(part(token, match[2], "rate"))
                        text = match[1]
                expanded.append(part(token, text, "description"))
                continue
            # A tax rate and amount can be returned in one OCR box.
            match = re.fullmatch(r"(\d+\.\d{2})\s+(\d[\d,]*\.\d{2})", text)
            tax_pair = next((prefix for prefix in ("cgst", "sgst")
                             if token["x0"] < centres[prefix + "_percent"] < token["x1"]
                             and token["x0"] < centres[prefix + "_amount"] < token["x1"]), None)
            if match and tax_pair:
                expanded.extend([part(token, match[1], tax_pair + "_percent"),
                                 part(token, match[2], tax_pair + "_amount")])
            else:
                expanded.append(token)
        columns, _ = _assign_columns(expanded, roles, centres)
        description = _description(columns["description"])
        numbers = {role: _labelled_column_number(columns[role]) for role in roles
                   if role not in ("description", "hsn", "serial")}
        if not description or not any(c.isalpha() for c in description) or any(v is None for v in numbers.values()):
            warnings.append("Skipped box/piece row %s: a required value was unreadable." % anchor["text"])
            continue
        boxes, pieces = numbers["case"], numbers["quantity"]
        if (boxes > 0) == (pieces > 0):
            warnings.append("Skipped box/piece row %s: mixed or zero quantities need review." % anchor["text"])
            continue
        quantity = boxes or pieces
        gross = quantity * numbers["rate"]
        taxable = numbers["taxable"]
        discount = numbers["discount"]
        gst = numbers["cgst_percent"] + numbers["sgst_percent"]
        if (gross <= 0 or not 0 <= discount <= gross or not 0 <= gst <= 100
                or abs(gross - discount - taxable) > max(.10, taxable * .0001)
                or any(abs(taxable * numbers[prefix + "_percent"] / 100 - numbers[prefix + "_amount"])
                       > max(.10, taxable * .0001) for prefix in ("cgst", "sgst"))
                or abs(taxable + numbers["cgst_amount"] + numbers["sgst_amount"] - numbers["net"])
                       > max(.10, numbers["net"] * .0001)):
            warnings.append("Skipped box/piece row %s: printed quantities, prices and totals disagree." % anchor["text"])
            continue
        extracted.append({
            "description": description, "vendor_product_code": None, "barcode": None,
            "hsn_code": _hsn_code(columns.get("hsn", [])), "quantity": quantity,
            "free_quantity": 0.0, "purchase_rate": numbers["rate"],
            "discount_percent": round(discount / gross * 100, 4), "gst_percent": gst,
            "confidence": round(sum(t["score"] for t in row) / max(len(row), 1) * 100, 2),
        })
        if abs(gross - discount - taxable) > .02:
            warnings.append("Row %s has a rounded printed unit price; verify its line total before confirming." % anchor["text"])
        if boxes:
            warnings.append("Row %s is priced per box; verify units per purchase quantity before creating the purchase order." % anchor["text"])
    return extracted, warnings


def _extract_table_lines(tokens, page_width):
    header = _find_table_header(tokens)
    if not header:
        return [], ["Product-table header was not detected."]
    product_token, header_tokens, median_height = header
    box_piece_result = _extract_box_piece_table_lines(tokens, header)
    if box_piece_result is not None:
        return box_piece_result
    rows_per_item = max(
        1, min(int((_OCR_TEMPLATE.get() or {}).get("data_rows_per_item") or 1), 3)
    )
    header_roles = {_table_header_role(token["text"]) for token in header_tokens}
    # Safe auto-detection for newly encountered vendor layouts: a secondary
    # quantity/weight row together with component-tax headings is a strong
    # signal that every product occupies two printed rows. Saving 2 on the
    # vendor template remains the explicit override for future bills.
    if (
        rows_per_item == 1
        and "secondary_quantity" in header_roles
        and {"rate", "quantity"}.issubset(header_roles)
        and header_roles.intersection({"cgst_percent", "sgst_percent", "igst_percent"})
    ):
        rows_per_item = 2
    if rows_per_item > 1:
        return _extract_stacked_table_lines(
            tokens, page_width, header, rows_per_item
        )
    # A Cases column alone does not imply SNK's fixed fifteen-column layout.
    if not {"upc", "case", "taxable", "gst"}.issubset(header_roles):
        return _extract_labelled_table_lines(tokens, page_width, header)
    table_left = max(0.0, min(token["x0"] for token in header_tokens) - median_height)
    table_right = min(
        page_width,
        max(token["x1"] for token in header_tokens) + median_height,
    )
    if table_right - table_left < page_width * 0.65:
        table_left, table_right = 0.0, page_width
    roles, centres, detected_headers = _column_centres(
        header_tokens, table_left, table_right
    )
    _header_columns, boundaries = _assign_columns(header_tokens, roles, centres)
    header_slope = _table_header_slope(header_tokens, page_width)
    serial_x = centres["serial"]

    labelled_header_tokens = [
        token for token in header_tokens if _is_table_header_label(token)
    ]

    def preliminary_y(token, edge="centre"):
        value = token["yc"]
        if edge == "top":
            value = token["y0"]
        elif edge == "bottom":
            value = token["y1"]
        return value - header_slope * (token["xc"] - serial_x)

    header_bottom = max(
        preliminary_y(token)
        for token in labelled_header_tokens or [product_token]
    ) + median_height * 0.15

    summary_token = None
    for token in sorted(tokens, key=lambda item: item["yc"]):
        if preliminary_y(token) <= header_bottom + median_height * 2:
            continue
        key = _normalise_key(token["text"])
        if key in ("TOTAL", "PARTICULARS", "மொத்தம்", "மொத்ததொகை", "கூடுதல்") and token["xc"] < boundaries[3]:
            summary_token = token
            break
    summary_y = (
        preliminary_y(summary_token, "top")
        if summary_token
        else max(preliminary_y(token, "bottom") for token in tokens)
    )

    serial_right = boundaries[0]
    serial_gap = max(centres["hsn"] - centres["serial"], median_height)
    serial_left = max(table_left, centres["serial"] - serial_gap * 1.5)
    serial_anchors = []
    for token in tokens:
        if not (header_bottom < preliminary_y(token) < summary_y):
            continue
        if not (serial_left <= token["xc"] < serial_right):
            continue
        key = _normalise_key(token["text"])
        if not key.isdigit():
            continue
        number = int(key)
        if 0 < number < 500:
            serial_anchors.append(
                {
                    "y": token["yc"],
                    "x": token["xc"],
                    "number": number,
                    "score": token["score"],
                    "inferred": False,
                }
            )
    serial_anchors.sort(key=lambda item: item["y"])

    deduplicated = []
    for anchor in serial_anchors:
        if deduplicated and abs(anchor["y"] - deduplicated[-1]["y"]) < median_height * 0.7:
            if anchor["score"] > deduplicated[-1]["score"]:
                deduplicated[-1] = anchor
        else:
            deduplicated.append(anchor)
    serial_anchors = _complete_row_anchors(deduplicated, median_height)

    # Phone photos often crop the narrow serial-number column. HSN is a much
    # wider, repeated identifier and provides a reliable row baseline in that
    # situation. Prefer it when it finds materially more rows than serials.
    hsn_candidates = []
    for token in tokens:
        if not (header_bottom < preliminary_y(token) < summary_y):
            continue
        if not (token["x0"] < boundaries[1] and token["x1"] > boundaries[0]):
            continue
        if not _code_digits(token["text"]):
            continue
        hsn_candidates.append({
            "y": token["yc"],
            "x": token["xc"],
            "number": 0,
            "score": token["score"],
            "inferred": True,
        })
    hsn_anchors = []
    for candidate in sorted(hsn_candidates, key=lambda item: item["y"]):
        if hsn_anchors and abs(candidate["y"] - hsn_anchors[-1]["y"]) < median_height * 0.7:
            if candidate["score"] > hsn_anchors[-1]["score"]:
                hsn_anchors[-1] = candidate
        else:
            hsn_anchors.append(candidate)
    for number, anchor in enumerate(hsn_anchors, start=1):
        anchor["number"] = number

    using_hsn_anchors = (
        not serial_anchors or len(hsn_anchors) >= len(serial_anchors) + 2
    )
    if using_hsn_anchors:
        anchors = list(hsn_anchors)
        for serial_anchor in serial_anchors:
            if any(
                abs(anchor["y"] - serial_anchor["y"]) < median_height * 0.7
                for anchor in anchors
            ):
                continue
            anchors.append(
                {
                    **serial_anchor,
                    "x": centres["hsn"],
                    "inferred": True,
                }
            )
        anchors.sort(key=lambda item: item["y"])
        for number, anchor in enumerate(anchors, start=1):
            anchor["number"] = number
    else:
        anchors = serial_anchors
    if not anchors:
        return [], ["Product rows were not detected below the table header."]

    row_spacings = [
        second["y"] - first["y"]
        for first, second in zip(anchors, anchors[1:])
        if second["y"] > first["y"]
    ]
    row_spacing = _median(row_spacings, median_height)
    serial_x = centres["hsn"] if using_hsn_anchors else centres["serial"]
    row_anchor_right = boundaries[1] if using_hsn_anchors else serial_right
    raw_header_top = min(token["y0"] for token in header_tokens)
    raw_summary_y = (
        summary_token["y0"]
        if summary_token
        else max(token["y1"] for token in tokens)
    )
    row_slope = _estimate_row_slope(
        tokens,
        anchors,
        serial_x,
        row_spacing,
        raw_header_top,
        raw_summary_y,
        row_anchor_right,
    )
    def adjusted_y(token):
        return token["yc"] - row_slope * (token["xc"] - serial_x)

    header_bottom = max(
        adjusted_y(token)
        for token in labelled_header_tokens or [product_token]
    ) + median_height * 0.15
    extracted_lines = []
    warnings = []
    has_barcode_header = "barcode" in detected_headers
    previous_local_slope = None
    for index, anchor in enumerate(anchors):
        warning_count_before_row = len(warnings)
        local_slope = (
            row_slope
            if using_hsn_anchors
            else _estimate_local_row_slope(
                tokens,
                anchor,
                row_spacing,
                table_left,
                table_right,
                preferred_slope=previous_local_slope,
            )
        )
        previous_local_slope = local_slope
        anchor_x = anchor.get("x") or serial_x
        pivot_x = centres["mrp"]
        if using_hsn_anchors:
            source_anchor_x = anchor_x
            target_anchor_x = centres["hsn"]
            row_x_scale = 1.0
        else:
            source_anchor_x = None
            target_anchor_x = None
            scale_denominator = serial_x - pivot_x
            row_x_scale = (
                (anchor_x - pivot_x) / scale_denominator
                if abs(scale_denominator) > 1
                else 1.0
            )
            row_x_scale = min(1.5, max(0.75, row_x_scale))

        def normalized_x(token):
            return pivot_x + (token["xc"] - pivot_x) / row_x_scale

        neighbour_gaps = []
        if index:
            neighbour_gaps.append(anchor["y"] - anchors[index - 1]["y"])
        if index + 1 < len(anchors):
            neighbour_gaps.append(anchors[index + 1]["y"] - anchor["y"])
        row_tolerance = min(
            row_spacing * 0.48,
            min(neighbour_gaps, default=row_spacing) * 0.46,
        )

        def local_adjusted_y(token):
            return token["yc"] - local_slope * (token["xc"] - anchor_x)

        row_tokens = [
            token
            for token in tokens
            if abs(local_adjusted_y(token) - anchor["y"]) <= row_tolerance
            and table_left <= normalized_x(token) <= table_right
        ]
        if using_hsn_anchors:
            expected_upc_span = centres["upc"] - centres["hsn"]
            upc_candidates = [
                token
                for token in row_tokens
                if re.fullmatch(r"\d{2,4}", _normalise_key(token["text"]))
                and anchor_x + expected_upc_span * 0.55
                <= token["xc"]
                <= anchor_x + expected_upc_span * 1.55
            ]
            if upc_candidates and expected_upc_span > 1:
                upc_token = min(
                    upc_candidates,
                    key=lambda token: abs(
                        token["xc"] - (anchor_x + expected_upc_span)
                    ),
                )
                row_x_scale = min(
                    1.35,
                    max(0.80, (upc_token["xc"] - anchor_x) / expected_upc_span),
                )
        columns, _boundaries = _assign_columns(
            row_tokens,
            roles,
            centres,
            x_scale=row_x_scale,
            pivot_x=pivot_x,
            source_anchor_x=source_anchor_x,
            target_anchor_x=target_anchor_x,
        )
        description = _description(columns["description"])
        case_quantity, free_cases = _parse_quantity(columns["case"])
        piece_quantity, free_pieces = _parse_quantity(columns["quantity"])
        units_per_case = max(_last_number(columns["upc"]), 0.0)
        quantity = piece_quantity + case_quantity * units_per_case
        free_quantity = free_pieces + free_cases * units_per_case
        rate = max(_last_number(columns["rate"]), 0.0)
        if not description or (quantity <= 0 and free_quantity <= 0):
            warnings.append(
                "Skipped OCR row %s because its product name or quantity was unclear."
                % anchor["number"]
            )
            continue

        scheme_discount = max(_last_number(columns["scheme_discount"]), 0.0)
        cash_discount = max(_last_number(columns["cash_discount"]), 0.0)
        # Perspective can place the narrow GST-rate token at the edge of the
        # taxable column. The taxable amount is the largest value in that
        # column, not the trailing 5/18 percent token.
        taxable_amount = max(_numeric_values(columns["taxable"]), default=0.0)
        calculated_rate = (
            (taxable_amount + scheme_discount + cash_discount) / quantity
            if quantity and taxable_amount
            else 0.0
        )
        if calculated_rate and (
            not rate or abs(rate - calculated_rate) / calculated_rate > 0.03
        ):
            # Decimal points are among the smallest marks in phone photos and
            # are occasionally missed (2.60 -> 260).  The printed taxable
            # amount provides a reliable local cross-check.
            rate = round(calculated_rate, 2)
            warnings.append(
                "Unit price was reconstructed from the taxable amount for OCR row %s; verify its price and quantity."
                % anchor["number"]
            )
        gross = quantity * rate
        discount_percent = (
            min(100.0, ((scheme_discount + cash_discount) / gross) * 100.0)
            if gross
            else 0.0
        )
        barcode = None
        if has_barcode_header:
            barcode_candidates = [
                re.sub(r"\D", "", token["text"])
                for token in columns.get("upc", [])
            ]
            barcode = next(
                (value for value in barcode_candidates if 8 <= len(value) <= 14),
                None,
            )
        meaningful_tokens = [token for token in row_tokens if token["text"]]
        average_score = (
            sum(token["score"] for token in meaningful_tokens) / len(meaningful_tokens)
            if meaningful_tokens
            else 0.0
        )
        completeness = sum(
            bool(value)
            for value in (description, quantity, rate, _hsn_code(columns["hsn"]))
        ) / 4
        gst_nearby = [
            token
            for token in tokens
            if boundaries[9] <= normalized_x(token) < boundaries[13]
            and abs(local_adjusted_y(token) - anchor["y"]) <= row_tolerance
        ]
        printed_gst = _labelled_column_number(columns["gst"])
        if printed_gst is not None and not 0 <= printed_gst <= 100:
            printed_gst = None
        gst_percent = printed_gst if printed_gst is not None else _gst_rate(columns["gst"] + gst_nearby)
        if not gst_percent and printed_gst is None:
            gst_percent = _gst_from_tax_amounts(gst_nearby)
        if taxable_amount:
            cgst_amount = max(_last_number(columns["cgst"]), 0.0)
            sgst_amount = max(_last_number(columns["sgst"]), 0.0)
            calculated_gst = ((cgst_amount + sgst_amount) / taxable_amount) * 100
            common_total_rates = (3.0, 5.0, 12.0, 18.0, 28.0)
            nearest_rate = min(
                common_total_rates, key=lambda value: abs(value - calculated_gst)
            )
            if (
                abs(nearest_rate - calculated_gst) <= 1.0
                and printed_gst is None
                and (
                    not gst_percent
                    or abs(gst_percent - nearest_rate) > 1.0
                )
            ):
                gst_percent = nearest_rate
            elif printed_gst is not None and abs(calculated_gst - printed_gst) > 1.0:
                warnings.append(
                    "Printed tax percentage and tax amounts disagree for OCR row %s; review its tax."
                    % anchor["number"]
                )
        if printed_gst is None:
            warnings.append(
                "Tax percentage was not clearly readable for OCR row %s; verify its tax before import."
                % anchor["number"]
            )
        extracted_lines.append(
            {
                "description": description,
                "vendor_product_code": None,
                "barcode": barcode,
                "hsn_code": _hsn_code(columns["hsn"]),
                "mrp": max(_labelled_column_number(columns.get("mrp", [])) or 0.0, 0.0) if "mrp" in detected_headers else 0.0,
                "quantity": quantity,
                "free_quantity": free_quantity,
                "purchase_rate": rate,
                "discount_percent": round(discount_percent, 4),
                "gst_percent": gst_percent,
                "confidence": round(
                    average_score * completeness * (60 if len(warnings) > warning_count_before_row else 100), 2
                ),
            }
        )
    return extracted_lines, warnings


def _parse_page(tokens, width, height):
    lines = _group_lines(tokens)
    full_text = "\n".join(line["text"] for line in lines)
    compact_upper = re.sub(r"[^A-Z0-9]", "", full_text.upper())
    template = _OCR_TEMPLATE.get() or {}
    gstin_match = _GSTIN_RE.search(compact_upper)
    bill_number = _bill_number_from_tokens(tokens) or _bill_number(lines[:30])
    extracted_lines, warnings = _extract_table_lines(tokens, width)
    return {
        "vendor_name": _vendor_name(lines, width),
        "vendor_tax_id": (
            template.get("vendor_tax_id")
            if template.get("strict")
            else (gstin_match.group(0) if gstin_match else None)
        ),
        "bill_number": bill_number,
        "bill_date": _bill_date(lines[:30]),
        "currency_code": "INR"
        if re.search(r"\b(?:GST|CGST|SGST|RUPEES?)\b|₹|ரூபாய்|ரூ\.|ஜிஎஸ்டி", full_text, re.IGNORECASE)
        else None,
        "notes": None,
        "lines": extracted_lines,
        "warnings": warnings,
        "raw_text": full_text,
        "width": width,
        "height": height,
    }


def _candidate_score(parsed):
    metadata = sum(
        bool(parsed.get(field))
        for field in ("vendor_name", "vendor_tax_id", "bill_number", "bill_date")
    )
    complete_lines = sum(
        bool(line.get("description") and line.get("quantity") and line.get("purchase_rate"))
        for line in parsed.get("lines", [])
    )
    return (complete_lines * 20 + len(parsed.get("lines", [])) * 5 + metadata * 3
            + parsed.get("_ocr_confidence", 0) - len(parsed.get("warnings", [])) * 6)


def _paddle_time_budget(deadline):
    # Windows CPU inference includes model startup. Leave one complete
    # reduced-resolution Tesseract pass plus overhead within the page budget.
    remaining = deadline - time.monotonic() if deadline is not None else OCR_PAGE_BUDGET_SECONDS
    return max(0.0, min(120.0, remaining - 20.0))


def _ocr_page_candidate(image, page_number, rectify, method_prefix, deadline=None, allow_paddle=True):
    backend = _get_tesseract_backend()
    candidates = []
    failures = []

    def add_reading(tokens, label):
        parsed = _parse_page(tokens, width, height)
        parsed["_rectified"] = rectified
        if any(token.get("incomplete") for token in tokens):
            parsed["warnings"].append("OCR retry time limit reached; some rows may be missing.")
        # A reliable Tamil reading must not lose its names to the English-only
        # alternative, even if that alternative invents more complete rows.
        parsed["_tamil_reading"] = (
            ("Tesseract" in label or "PaddleOCR" in label) and any(
                _has_tamil(token["text"]) and token["score"] >= .80
                and len(re.findall(r"[\u0b80-\u0bff]", token["text"])) >= 3
                for token in tokens
                if any(_has_tamil(line["description"]) for line in parsed["lines"])
            )
        )
        parsed["_ocr_confidence"] = (
            sum(token["score"] for token in tokens) / len(tokens) if tokens else 0
        )
        if tokens and parsed["_ocr_confidence"] < .70:
            parsed["warnings"].append("Low OCR confidence: review product names and numbers.")
        candidates.append((f"{method_prefix}, {label}", parsed, len(tokens)))
        return parsed

    def tesseract_read(prepared, psm, label):
        remaining = deadline - time.monotonic() if deadline else 25
        if remaining < 1:
            return None
        try:
            tokens = backend.run_tesseract(prepared, page=page_number, psm=psm,
                                           timeout=min(25, remaining))
            return add_reading(tokens, label)
        except backend.LocalOCRSetupError as error:
            failures.append(str(error))
            return None
        except backend.LocalOCRBackendError as error:
            failures.append(str(error))
            return None

    paddle = _get_paddle_backend()
    if allow_paddle and not paddle.available():
        reason = paddle.unavailable_reason()
        failures.append("PaddleOCR unavailable: " + reason)
        _logger.warning("PaddleOCR unavailable; extraction uses Tesseract: %s", reason)
    if allow_paddle and paddle.available() and _paddle_time_budget(deadline) >= 10:
        try:
            # Keep glyphs/decimal points intact for the neural reader. The
            # existing rotation loop supplies page orientation correction.
            # Paddle's detector caps its own input at 1280px. Avoid first
            # enlarging a phone photo to 3400px and then shrinking it again.
            # The Windows CPU model is accurate on invoice print at 1600px,
            # while 2200px can exceed the request budget during detection.
            original = _resize_for_ocr(image, target_width=1600, max_side=2000)
            remaining = _paddle_time_budget(deadline)
            _logger.info("Starting local PaddleOCR (%.1fs limit; 20s reserved for fallback)", remaining)
            rows = paddle.recognize(original, timeout=remaining)
            tokens = [_token(row["text"], row["score"], row["box"], page=page_number, source="paddleocr")
                      for row in rows if row["score"] >= MIN_OCR_SCORE]
            tokens = [token for token in tokens if token]
            # Paddle geometry belongs to the unaltered input, not to the
            # separately deskewed grayscale fallback.
            parsed = _parse_page(tokens, original.shape[1], original.shape[0])
            parsed["_tamil_reading"] = any(_has_tamil(line["description"]) for line in parsed["lines"])
            parsed["_ocr_confidence"] = sum(t["score"] for t in tokens) / max(len(tokens), 1)
            candidates.append((f"{method_prefix}, PaddleOCR Tamil+English", parsed, len(tokens)))
            # Paddle is the primary engine. Review warnings remain visible;
            # they are not a reason to spend the page budget on a second OCR.
            if parsed["lines"]:
                return candidates[-1]
        except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
            failures.append(str(error))
            _logger.warning("PaddleOCR returned no usable result; trying local fallback: %s", error)
    prepared = _crop_and_rectify_document(image) if rectify else image
    rectified = prepared is not image
    enhanced, degridded = _preprocess_variants(prepared, rectify=False, deskew=rectify)
    height, width = enhanced.shape[:2]
    tesseract_read(degridded, 11, "Tesseract Tamil+English, grid removed")

    best = _best_candidate(candidates) if candidates else None
    if (not best or len(best[1]["lines"]) < 3
            or (best[1].get("_tamil_reading")
                and any("Skipped" in warning for warning in best[1]["warnings"]))):
        tesseract_read(degridded, 6, "Tesseract Tamil+English, row layout")
        best = _best_candidate(candidates) if candidates else None
        if not best or not best[1]["lines"]:
            tesseract_read(enhanced, 11, "Tesseract Tamil+English, contrast enhanced")
    if not candidates:
        # A failed retry/orientation must not discard earlier usable results.
        add_reading([], "no text returned")
        if not failures:
            failures.append("No local OCR reader returned text for this orientation.")
    best = _best_candidate(candidates)
    best[1]["warnings"].extend(failures)
    return best


def _best_candidate(candidates):
    tamil_candidates = [candidate for candidate in candidates
                        if candidate[1].get("_tamil_reading") and candidate[1]["lines"]]
    return max(tamil_candidates or candidates, key=lambda item: _candidate_score(item[1]))


def _extract_bill(file_bytes, mimetype):
    """Extract a normalized bill dictionary without any external network call."""
    if not file_bytes:
        raise LocalOCRError("The uploaded bill is empty.")
    file_deadline = time.monotonic() + OCR_FILE_BUDGET_SECONDS

    page_inputs = (
        _pdf_pages(file_bytes)
        if mimetype == "application/pdf"
        else [(_image_from_bytes(file_bytes), [])]
    )
    parsed_pages = []
    audit_pages = []
    for page_number, (image, native_tokens) in enumerate(page_inputs, start=1):
        deadline = min(file_deadline, time.monotonic() + OCR_PAGE_BUDGET_SECONDS)
        candidates = []
        if native_tokens:
            native_width = max((token["x1"] for token in native_tokens), default=1.0)
            native_height = max((token["y1"] for token in native_tokens), default=1.0)
            candidates.append(
                (
                    "Fitz/PyMuPDF native text",
                    _parse_page(native_tokens, native_width, native_height),
                    len(native_tokens),
                )
            )

        # Embedded PDF text already includes Unicode and exact word geometry.
        # Only OCR a page when its native text did not reconstruct a table.
        if image is not None:
            backend = _get_tesseract_backend()
            suggested_rotation = None
            try:
                backend.get_backend_info()
                suggested_rotation = backend.get_orientation(
                    _resize_for_ocr(image, target_width=1400, max_side=1800),
                    timeout=min(8, max(0.1, deadline - time.monotonic())),
                )
            except backend.LocalOCRBackendError as error:
                if not _get_paddle_backend().available():
                    raise LocalOCRError(str(error)) from error
                _logger.warning("Tesseract orientation/fallback unavailable; using PaddleOCR: %s", error)
            for orientation_name, oriented_image in _orientation_variants(image, suggested_rotation):
                if time.monotonic() >= deadline:
                    break
                orientation_candidates = [
                    _ocr_page_candidate(
                        oriented_image,
                        page_number,
                        rectify=mimetype != "application/pdf",
                        method_prefix=f"Local OCR {orientation_name}, prepared",
                        deadline=deadline,
                    )
                ]
                orientation_best = _best_candidate(orientation_candidates)
                if (
                    mimetype != "application/pdf"
                    and deadline - time.monotonic() > 8
                    and len(orientation_best[1].get("lines", [])) < 3
                ):
                    orientation_candidates.append(
                        _ocr_page_candidate(
                            oriented_image,
                            page_number,
                            rectify=False,
                            method_prefix=f"Local OCR {orientation_name}, unwarped",
                            deadline=deadline,
                            allow_paddle=False,
                        )
                    )
                    orientation_best = _best_candidate(orientation_candidates)
                candidates.extend(orientation_candidates)
                # Stop when both a table and invoice metadata are readable.
                if (
                    len(orientation_best[1].get("lines", [])) >= 1
                    and (
                        orientation_best[1].get("bill_number")
                        or orientation_best[1].get("bill_date")
                    )
                ):
                    break

        if not candidates:
            raise LocalOCRError("OCR time limit reached. Upload fewer pages or a clearer bill photo.")
        method, parsed, detected_words = _best_candidate(candidates)
        if time.monotonic() >= deadline:
            parsed["warnings"].append("OCR retry time limit reached; some rows may be missing.")
        parsed_pages.append(parsed)
        audit_pages.append(
            {
                "page": page_number,
                "selected_method": method,
                "detected_words": detected_words,
                "mean_word_confidence": round(parsed.get("_ocr_confidence", 1.0), 3),
                "raw_text": parsed["raw_text"],
                "warnings": parsed["warnings"],
            }
        )

    if not parsed_pages:
        raise LocalOCRError("No pages could be read from the uploaded bill.")
    first = parsed_pages[0]
    all_lines = [line for page in parsed_pages for line in page["lines"]]
    if not all_lines:
        raise LocalOCRError(
            "OCR found text, but no product table rows could be reconstructed. "
            "Use a sharper, straight photo that shows the full table."
        )

    fingerprint = hashlib.sha256(file_bytes).hexdigest()[:16]
    review_warnings = list(dict.fromkeys(
        "Page %s: %s" % (index, warning)
        for index, page in enumerate(parsed_pages, start=1)
        for warning in page["warnings"]
    ))
    result = {
        "vendor_name": first["vendor_name"],
        "vendor_tax_id": first["vendor_tax_id"],
        "bill_number": first["bill_number"],
        "bill_date": first["bill_date"],
        "currency_code": first["currency_code"],
        "notes": "OCR review:\n" + "\n".join(review_warnings) if review_warnings else None,
        "lines": all_lines,
        "_audit": {
            "engine": "PaddleOCR / Tesseract Tamil+English / Fitz-PyMuPDF",
            "languages": ["tam", "eng"],
            "local_only": True,
            "file_fingerprint": fingerprint,
            "pages": audit_pages,
        },
    }
    return result, result["_audit"]["engine"], fingerprint


def extract_bill(file_bytes, mimetype, header_aliases=None, template_config=None):
    """Extract a bill while applying request-local configurable headings."""
    template_config = template_config or None
    aliases = (
        template_config.get("aliases", {})
        if template_config is not None
        else (header_aliases or {})
    )
    token = _HEADER_ALIASES.set(aliases)
    template_token = _OCR_TEMPLATE.set(template_config)
    try:
        return _extract_bill(file_bytes, mimetype)
    finally:
        _OCR_TEMPLATE.reset(template_token)
        _HEADER_ALIASES.reset(token)


def extract_product_name(file_bytes):
    """Read a name crop without requiring invoice metadata or table columns."""
    import cv2
    image = _image_from_bytes(file_bytes)
    image = _resize_for_ocr(image, target_width=1400, max_side=2000)
    image = cv2.copyMakeBorder(image, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    paddle = _get_paddle_backend()
    failure = ""
    if paddle.available():
        try:
            rows = paddle.recognize(image, timeout=90)
            tokens = [_token(row["text"], row["score"], row["box"])
                      for row in rows if row["score"] >= MIN_OCR_SCORE]
            text = _normalise_space(" ".join(line["text"] for line in _group_lines([t for t in tokens if t])))
            if text:
                return {"text": text, "engine": "PaddleOCR Tamil+English"}
        except (RuntimeError, ValueError, OSError, KeyError, TypeError) as error:
            failure = str(error)
    backend = _get_tesseract_backend()
    try:
        for psm in (7, 6):
            tokens = backend.run_tesseract(image, page=1, psm=psm, timeout=20)
            text = _normalise_space(" ".join(line["text"] for line in _group_lines(tokens)))
            if text:
                return {"text": text, "engine": "Tesseract Tamil+English"}
    except backend.LocalOCRBackendError as error:
        raise LocalOCRError(str(error)) from error
    raise LocalOCRError("No product name could be read. Select a clearer, tighter crop around the name."
                        + (" " + failure if failure else ""))
