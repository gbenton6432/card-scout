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


def _peak(profile, lo, hi):
    """Strongest edge in profile[lo:hi] -> (index, strength vs. that zone's median)."""
    lo, hi = max(0, lo), min(len(profile), hi)
    seg = profile[lo:hi]
    if len(seg) < 3:
        return None, 0.0
    # The first strong edge from the outside is the border line; a frame line's far side or
    # artwork further in can be stronger but isn't where the border ends.
    peak = float(seg.max())
    i = int(np.argmax(seg >= 0.5 * peak))
    while i + 1 < len(seg) and seg[i + 1] >= seg[i]:  # climb to the top of that edge
        i += 1
    return lo + i, float(seg[i] / (np.median(seg) + 1e-6))


def _side_checks(gray, find, slices):
    """Run `find(a, b)` -> (pos, strength) on the middle of a side and on three slices along it.
    Returns (pos, strength, spread) where spread is how far the three slice readings disagree."""
    pos, strength = find(.2, .8)
    vals = [find(a, b)[0] for a, b in slices]
    vals = [v for v in vals if v is not None]
    spread = (max(vals) - min(vals)) if len(vals) == 3 else 1e9
    return pos, strength, spread


SLICES = ((.15, .35), (.4, .6), (.65, .85))


def _inner_lines(card):
    """Card outline found = the card's edge: look inward for the border/artwork line on each side.
    Returns {side: (width_px, ok)}."""
    gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    h, w = gray.shape
    sx, sy, mx, my = int(w * .015), int(h * .015), int(w * .22), int(h * .22)
    finders = {
        "l": (lambda a, b: _peak(np.median(gx[int(h * a):int(h * b), :], 0), sx, mx), w),
        "r": (lambda a, b: _peak(np.median(gx[int(h * a):int(h * b), ::-1], 0), sx, mx), w),
        "t": (lambda a, b: _peak(np.median(gy[:, int(w * a):int(w * b)], 1), sy, my), h),
        "b": (lambda a, b: _peak(np.median(gy[::-1, int(w * a):int(w * b)], 1), sy, my), h),
    }
    out = {}
    for side, (find, dim) in finders.items():
        pos, strength, spread = _side_checks(gray, find, SLICES)
        if pos is None:
            out[side] = (None, False)
            continue
        # the band between the card edge and the line should be an even color (a printed border)
        # stay clear of the edge shading and the line's own blur on either side of the band
        end = max(pos - max(4, int(pos * .15)), sx + 3)
        if side == "l":
            band = gray[int(h * .3):int(h * .7), sx + 2:end]
        elif side == "r":
            band = gray[int(h * .3):int(h * .7), w - end:w - sx - 2]
        elif side == "t":
            band = gray[sy + 2:end, int(w * .3):int(w * .7)]
        else:
            band = gray[h - end:h - sy - 2, int(w * .3):int(w * .7)]
        even = band.size == 0 or float(np.std(band)) <= 32
        out[side] = (pos, strength >= 2.0 and spread <= .03 * dim and even)
    return out


def _outer_edges(img, quad, W_=W, H_=H):
    """Card outline found might be the border's inner frame line (on a light background the card's
    real edge is faint). Look just outside it for a parallel card edge with an even border band.
    Returns ({side: (width_px, ok)}, canvas transform) in a canvas where the found outline is a
    W_ x H_ rectangle."""
    ex, ey = int(W_ * .16), int(H_ * .16)
    dst = np.float32([[ex, ey], [ex + W_, ey], [ex + W_, ey + H_], [ex, ey + H_]])
    M = cv2.getPerspectiveTransform(quad, dst)
    canvas = cv2.warpPerspective(img, M, (W_ + 2 * ex, H_ + 2 * ey), borderMode=cv2.BORDER_REPLICATE)
    gray = cv2.cvtColor(canvas, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))
    lo_x, hi_x, lo_y, hi_y = int(W_ * .012), int(W_ * .14), int(H_ * .012), int(H_ * .14)
    rows = lambda a, b: slice(ey + int(H_ * a), ey + int(H_ * b))
    cols = lambda a, b: slice(ex + int(W_ * a), ex + int(W_ * b))
    # profiles run outward from the found outline; index = distance outside it
    finders = {
        "l": (lambda a, b: _peak(np.median(gx[rows(a, b), :ex][:, ::-1], 0), lo_x, hi_x), W_),
        "r": (lambda a, b: _peak(np.median(gx[rows(a, b), ex + W_:], 0), lo_x, hi_x), W_),
        "t": (lambda a, b: _peak(np.median(gy[:ey, cols(a, b)][::-1], 1), lo_y, hi_y), H_),
        "b": (lambda a, b: _peak(np.median(gy[ey + H_:, cols(a, b)], 1), lo_y, hi_y), H_),
    }
    out, bands = {}, {}
    for side, (find, dim) in finders.items():
        pos, strength, spread = _side_checks(gray, find, SLICES)
        if pos is None:
            out[side] = (None, False)
            continue
        if side == "l":
            band = gray[rows(.3, .7), ex - pos + 2:ex - 2]
        elif side == "r":
            band = gray[rows(.3, .7), ex + W_ + 2:ex + W_ + pos - 2]
        elif side == "t":
            band = gray[ey - pos + 2:ey - 2, cols(.3, .7)]
        else:
            band = gray[ey + H_ + 2:ey + H_ + pos - 2, cols(.3, .7)]
        even = band.size > 0 and float(np.std(band)) <= 22
        bands[side] = float(np.mean(band)) if band.size else None
        out[side] = (pos, strength >= 2.5 and spread <= .025 * dim and even)
    # opposite sides of a real border are the same color (allowing for uneven lighting)
    for a, b in (("l", "r"), ("t", "b")):
        if out[a][1] and out[b][1] and abs(bands[a] - bands[b]) > 35:
            out[a], out[b] = (out[a][0], False), (out[b][0], False)
    return out, M, (ex, ey)


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
    out = {}

    def axis(s1, s2):
        (a, ok_a), (b, ok_b) = s1, s2
        if not (ok_a and ok_b) or not a or not b:
            return None
        v = float(100 * a / (a + b))
        return v if max(v, 100 - v) <= 70 else None  # beyond 70/30 is almost always a misread

    # 1) The outline found may be the border's inner frame line (a gray card on a light
    #    background has a faint edge). If a consistent card edge sits just outside it on both
    #    sides of an axis, widen the outline to the real card edge on those sides.
    outer, M, (ex, ey) = _outer_edges(img, quad, tw, th)
    card_quad, method = quad, "edge"
    grow = {k: 0 for k in "lrtb"}
    for a, b in (("l", "r"), ("t", "b")):
        if outer[a][1] and outer[b][1]:
            grow[a], grow[b] = outer[a][0], outer[b][0]
    if any(grow.values()):
        method = "frame"
        pts = np.float32([[ex - grow["l"], ey - grow["t"]], [ex + tw + grow["r"], ey - grow["t"]],
                          [ex + tw + grow["r"], ey + th + grow["b"]], [ex - grow["l"], ey + th + grow["b"]]])
        card_quad = cv2.perspectiveTransform(pts[None], np.linalg.inv(M))[0].astype(np.float32)

    # 2) From the card edge, look inward for the border line on each side.
    M2 = cv2.getPerspectiveTransform(card_quad, np.float32([[0, 0], [tw, 0], [tw, th], [0, th]]))
    inner = _inner_lines(cv2.warpPerspective(img, M2, (tw, th)))
    lr, tb = axis(inner["l"], inner["r"]), axis(inner["t"], inner["b"])

    out["quad"] = [[round(float(x) / w, 4), round(float(y) / h, 4)] for x, y in card_quad]
    if lr is None and tb is None:
        out.update(confidence=0, note="no consistent border found (borderless or full-art design?), check by eye")
        return out

    def fmt(v):
        a = round(max(v, 100 - v)); return f"{a}/{100 - a}"

    notes = []
    if lr is None:
        notes.append("top/bottom only; left/right unclear")
    if tb is None:
        notes.append("left/right only; top/bottom unclear")
    parts = [x for x in (lr, tb) if x is not None]
    worst = max(max(v, 100 - v) for v in parts)
    conf = float(min(1.0, 0.45 + find_conf)) * (1.0 if len(parts) == 2 else 0.85)
    out.update(
        lr=round(lr, 1) if lr is not None else None, tb=round(tb, 1) if tb is not None else None,
        worst=round(worst, 1),
        centering=" · ".join(x for x in (f"{fmt(lr)} LR" if lr is not None else "", f"{fmt(tb)} TB" if tb is not None else "") if x),
        psa10_ok=bool(worst <= 55.5), confidence=round(conf, 2), note=", ".join(notes), method=method,
    )
    return out
