"""
Reads a serial number stamp (e.g. 023/199, 1/1) off the card photo itself, not the title.

Uses RapidOCR (free, runs on the GitHub runner's CPU, about 0.25 s a photo). Returns
(numerator, denominator, confidence) or None. Image quality limits it: dark, foil-glared or
tiny stamps can misread, so the dashboard labels these as read from the photo.
"""
import re
import threading

import cv2
import numpy as np

_local = threading.local()
_PAT = re.compile(r"(?<![\d/])(\d{1,4})\s*[/\\|]\s*(\d{1,4})(?![\d/])")


def _engine():
    if not hasattr(_local, "ocr"):
        from rapidocr_onnxruntime import RapidOCR
        _local.ocr = RapidOCR()
        # Serial stamps are printed upright; the rotation guesser flips some (099/199 -> 661/660)
        _local.ocr.use_angle_cls = False
    return _local.ocr


def read_serial(image_bytes, quad=None):
    img = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    if quad:  # crop to the card so nothing in the background is read
        pts = np.array(quad, np.float32) * [w, h]
        x0, y0 = np.maximum(pts.min(0) - 10, 0).astype(int)
        x1, y1 = np.minimum(pts.max(0) + 10, [w, h]).astype(int)
        if (x1 - x0) > 100 and (y1 - y0) > 100:
            img = img[y0:y1, x0:x1]
    s = 1100 / max(img.shape[:2])
    if s < 1:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    result, _ = _engine()(img)
    best = None
    for _box, text, conf in result or []:
        for m in _PAT.finditer(text.replace(" ", "") if len(text) < 12 else text):
            num, den = int(m.group(1)), int(m.group(2))
            # A serial is n/d with 1 <= n <= d; skip dates (e.g. 4/12 with d <= 12 is still allowed
            # only when it's alone in its text box, as stamps are)
            if not (1 <= num <= den <= 9999):
                continue
            if den <= 31 and len(text.strip()) > 8:
                continue
            score = float(conf) + (0.1 if len(text.strip()) <= 9 else 0)
            if not best or score > best[2]:
                best = (num, den, round(float(conf), 2))
    return best
