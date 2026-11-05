"""
Free, local centering measurement for card photos (OpenCV only).

measure(image_bytes) -> dict with:
  quad        card corners in the original photo, normalised 0-1, order TL,TR,BR,BL
  lr, tb      left/right and top/bottom splits, e.g. 52.3 means 52.3/47.7
  centering   display string like "52/48 LR · 55/45 TB"
  worst       the worse of the two splits (larger side), lower is better (50 = perfect)
  psa10_ok    True if both splits are within PSA's 55/45 front standard
  confidence  0-1, how sure the measurement is
  note        why confidence is low, if it is
"""
import cv2
import numpy as np

W, H = 630, 880  # warped card size (2.5 x 3.5 ratio)


def _order(pts):
    pts = np.array(pts, dtype=np.float32).reshape(4, 2)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32)


def _find_card(img):
    """Return (quad, confidence) of the largest card-shaped outline."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    best, best_score = None, 0.0
    for lo, hi in ((30, 90), (60, 160), (15, 50)):
        edges = cv2.dilate(cv2.Canny(gray, lo, hi), np.ones((3, 3), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if area < 0.12 * w * h:
                continue
            peri = cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, 0.02 * peri, True)
            four = len(approx) == 4
            quad = approx if four else cv2.boxPoints(cv2.minAreaRect(c))
            q = _order(quad)
            side_w = np.linalg.norm(q[1] - q[0]); side_h = np.linalg.norm(q[3] - q[0])
            if min(side_w, side_h) == 0:
                continue
            ratio = max(side_w, side_h) / min(side_w, side_h)
            ratio_fit = max(0.0, 1 - abs(ratio - 1.4) / 0.4)  # cards are 1.4:1
            touches = (q[:, 0].min() < 3 or q[:, 1].min() < 3 or
                       q[:, 0].max() > w - 3 or q[:, 1].max() > h - 3)
            score = (area / (w * h)) * (1.0 if four else 0.6) * ratio_fit * (0.5 if touches else 1.0)
            if score > best_score:
                best, best_score = q, score
    if best is None:
        return None, 0.0
    return best, min(1.0, best_score * 2.2)


def _border(profile, lo, hi):
    """Strongest edge in profile[lo:hi]; returns (index, strength ratio)."""
    seg = profile[lo:hi]
    if len(seg) == 0:
        return None, 0.0
    i = int(seg.argmax())
    strength = float(seg[i] / (np.median(profile) + 1e-6))
    return lo + i, strength


def _measure_borders(card):
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    h, w = gray.shape
    # sample the middle 60% of each side so corners/logos don't interfere
    col_prof = np.median(gx[int(h * .2):int(h * .8), :], axis=0)
    row_prof = np.median(gy[:, int(w * .2):int(w * .8)], axis=1)
    skip_x, skip_y = int(w * .015), int(h * .015)   # ignore the physical edge itself
    max_x, max_y = int(w * .22), int(h * .22)       # borders are never wider than this
    l, sl = _border(col_prof, skip_x, max_x)
    r_i, sr = _border(col_prof[::-1], skip_x, max_x)
    t, st = _border(row_prof, skip_y, max_y)
    b_i, sb = _border(row_prof[::-1], skip_y, max_y)
    return (l, r_i, t, b_i), min(sl, sr, st, sb)


def measure(image_bytes):
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return {"confidence": 0, "note": "image unreadable"}
    oh, ow = img.shape[:2]
    scale = 1200 / max(oh, ow)
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    h, w = img.shape[:2]

    quad, find_conf = _find_card(img)
    if quad is None:
        return {"confidence": 0, "note": "card outline not found (glare, slab or busy background)"}

    landscape = np.linalg.norm(quad[1] - quad[0]) > np.linalg.norm(quad[3] - quad[0])
    tw, th = (H, W) if landscape else (W, H)
    M = cv2.getPerspectiveTransform(quad, np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]))
    card = cv2.warpPerspective(img, M, (tw, th))

    (l, r, t, b), edge_strength = _measure_borders(card)
    out = {"quad": [[round(float(x) / w, 4), round(float(y) / h, 4)] for x, y in quad]}
    if None in (l, r, t, b) or min(l, r, t, b) <= 0:
        out.update(confidence=0, note="borders not found (full-art or borderless card?)")
        return out

    lr = float(100 * l / (l + r))
    tb = float(100 * t / (t + b))
    worst = max(lr, 100 - lr, tb, 100 - tb)

    conf = float(find_conf * min(1.0, edge_strength / 4))
    notes = []
    if edge_strength < 2.5:
        notes.append("border edge faint")
    if find_conf < 0.4:
        notes.append("card outline uncertain")
    if worst > 75:
        conf *= 0.4; notes.append("split looks implausible, check by eye")

    def fmt(v):
        a = round(max(v, 100 - v)); return f"{a}/{100 - a}"

    out.update(
        lr=round(lr, 1), tb=round(tb, 1), worst=round(worst, 1),
        centering=f"{fmt(lr)} LR · {fmt(tb)} TB",
        psa10_ok=bool(worst <= 55.5),
        confidence=round(conf, 2), note=", ".join(notes),
    )
    return out
