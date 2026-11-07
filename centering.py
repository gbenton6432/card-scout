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


def _intersect(l1, l2):
    (x1, y1, x2, y2), (x3, y3, x4, y4) = l1, l2
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-6:
        return None
    a, b = x1 * y2 - y1 * x2, x3 * y4 - y3 * x4
    return ((a * (x3 - x4) - (x1 - x2) * b) / d, (a * (y3 - y4) - (y1 - y2) * b) / d)


def _dist(seg, p):
    x1, y1, x2, y2 = seg
    return abs((y2 - y1) * p[0] - (x2 - x1) * p[1] + x2 * y1 - y2 * x1) / (np.hypot(x2 - x1, y2 - y1) + 1e-9)


def _find_card_lines(img):
    """Second way to find the card: long straight edges. Works when the card touches or
    stands on something (a stand, a mat) that merges with its outline. Lines may be tilted,
    so a card photographed at a slight angle still gives its four true corners. If one side
    is hidden (e.g. by a stand), it is placed from the other three using the card's 2.5x3.5
    shape and refined later by the edge search."""
    h, w = img.shape[:2]
    sc = min(1.0, 900 / max(h, w))
    small = cv2.resize(img, (int(w * sc), int(h * sc))) if sc < 1 else img.copy()
    sh, sw = small.shape[:2]
    g = cv2.bilateralFilter(small, 9, 60, 9)  # flattens fabric / wood texture, keeps card edges
    g = cv2.GaussianBlur(cv2.cvtColor(g, cv2.COLOR_BGR2GRAY), (7, 7), 0)
    edges = cv2.Canny(g, 20, 60)
    support = cv2.dilate(edges, np.ones((5, 5), np.uint8))
    segs = cv2.HoughLinesP(edges, 1, np.pi / 360, 50, minLineLength=int(0.15 * min(sh, sw)), maxLineGap=30)
    if segs is None:
        return None, 0.0
    # Group pieces of the same straight edge (a card edge is often broken by glare or a stand)
    groups = {}
    for x1, y1, x2, y2 in segs.reshape(-1, 4).astype(float):
        ang = np.degrees(np.arctan2(y2 - y1, x2 - x1)) % 180
        L = float(np.hypot(x2 - x1, y2 - y1))
        if ang < 10 or ang > 170:
            a_ = ang if ang < 90 else ang - 180
            pos = y1 + (y2 - y1) * (sw / 2 - x1) / (x2 - x1 + 1e-9)
            kind = "h"
        elif 80 < ang < 100:
            a_ = ang
            pos = x1 + (x2 - x1) * (sh / 2 - y1) / (y2 - y1 + 1e-9)
            kind = "v"
        else:
            continue
        k = (kind, round(pos / 6), round(a_ / 1.5))
        g_ = groups.setdefault(k, {"len": 0.0, "pts": [], "pos": pos})
        g_["len"] += L
        g_["pts"] += [(x1, y1), (x2, y2)]
    hor, ver = [], []
    for (kind, _, _), g_ in groups.items():
        vx, vy, x0, y0 = cv2.fitLine(np.array(g_["pts"], np.float32), cv2.DIST_L2, 0, 0.01, 0.01).ravel()
        seg = (x0 - 1000 * vx, y0 - 1000 * vy, x0 + 1000 * vx, y0 + 1000 * vy)
        (hor if kind == "h" else ver).append((g_["pos"], g_["len"], seg))

    def dedupe(lines):  # the 14 best-supported lines, in position order
        out = []
        for pos, L, seg in sorted(lines, key=lambda t: -t[1]):
            if all(abs(pos - p) > 6 for p, _, _ in out):
                out.append((pos, L, seg))
        return [seg for _, _, seg in sorted(out[:14])]

    hor, ver = dedupe(hor), dedupe(ver)

    def _on(a, b):
        n = 80
        xs = np.clip(np.linspace(a[0], b[0], n).astype(int), 0, sw - 1)
        ys = np.clip(np.linspace(a[1], b[1], n).astype(int), 0, sh - 1)
        return float((support[ys[8:-8], xs[8:-8]] > 0).mean())  # ends skipped: corners round or hidden

    def side_support(a, b):
        """Edge pixels on the side minus edge pixels just beside it: a real card edge is a
        clean line; fabric, wood grain or busy artwork has edges everywhere."""
        d = np.array(b, float) - np.array(a, float)
        nrm = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-9) * 7
        beside = max(_on(a + nrm, b + nrm), _on(a - nrm, b - nrm))
        return _on(a, b) - 0.8 * beside

    def shifted(seg, dx, dy):
        x1, y1, x2, y2 = seg
        return (x1 + dx, y1 + dy, x2 + dx, y2 + dy)

    cands = []
    H_ = hor + [None]; V_ = ver + [None]
    for i, top in enumerate(H_):
        for bot in H_[i + 1:] if top is not None else H_:
            for j, left in enumerate(V_):
                for right in V_[j + 1:] if left is not None else V_:
                    missing = [top, bot, left, right].count(None)
                    if missing == 0:
                        cands.append((top, bot, left, right, None))
                    elif missing == 1:
                        cands.append((top, bot, left, right, [k for k, x in enumerate((top, bot, left, right)) if x is None][0]))
    best, best_score = None, 0.0
    for top, bot, left, right, miss in cands:
        for shape in ((1.4, 1.0), (1.0, 1.4)) if miss is not None else ((None, None),):
            t, b_, l, r = top, bot, left, right
            if miss is not None:
                # length of the side we have that runs along the missing one
                if miss in (0, 1):
                    known = t if t is not None else b_
                    p1, p2 = _intersect(known, l), _intersect(known, r)
                    if p1 is None or p2 is None:
                        continue
                    span = np.hypot(p2[0] - p1[0], p2[1] - p1[1]) * shape[0] / shape[1]
                    if miss == 1:
                        b_ = shifted(t, 0, span)
                    else:
                        t = shifted(b_, 0, -span)
                else:
                    known = l if l is not None else r
                    p1, p2 = _intersect(t, known), _intersect(b_, known)
                    if p1 is None or p2 is None:
                        continue
                    span = np.hypot(p2[0] - p1[0], p2[1] - p1[1]) * shape[1] / shape[0]
                    if miss == 3:
                        r = shifted(l, span, 0)
                    else:
                        l = shifted(r, -span, 0)
            if miss is not None:
                # a real line where the hidden side would be: that candidate is tried with the line
                inferred = (t, b_, l, r)[miss]
                mid = ((inferred[0] + inferred[2]) / 2, (inferred[1] + inferred[3]) / 2)
                pool = hor if miss in (0, 1) else ver
                if any(_dist(seg, mid) < 0.04 * min(sw, sh) for seg in pool):
                    continue
            pts = [_intersect(t, l), _intersect(t, r), _intersect(b_, r), _intersect(b_, l)]
            if any(p is None for p in pts):
                continue
            q = np.array(pts, np.float32)
            if q[:, 0].min() < -2 or q[:, 1].min() < -2 or q[:, 0].max() > sw + 2 or q[:, 1].max() > sh + 2:
                continue
            cw = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
            ch = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
            if min(cw, ch) < 0.2 * min(sw, sh):
                continue
            ratio_fit = max(0.0, 1 - abs(max(cw, ch) / min(cw, ch) - 1.4) / 0.15)
            if not ratio_fit:
                continue
            sides = [(q[0], q[1]), (q[3], q[2]), (q[0], q[3]), (q[1], q[2])]  # top, bottom, left, right
            sup = [side_support(*sd) for k, sd in enumerate(sides) if k != miss]
            if min(sup) < 0.35:
                continue
            score = cw * ch / (sw * sh) * ratio_fit * float(np.mean(sup)) * min(sup) * (0.75 if miss is not None else 1.0)
            if score > best_score:
                best, best_score = q / sc, score
    if best is None:
        return None, 0.0
    return _order(best), min(1.0, best_score * 3.0)


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
    hh, ww = img.shape[:2]
    touches = quad is not None and (quad[:, 0].min() < 3 or quad[:, 1].min() < 3 or
                                    quad[:, 0].max() > ww - 3 or quad[:, 1].max() > hh - 3)
    if quad is None or touches:
        # The outline merged with the background (card on a stand or mat) or is weak: try straight edges
        q2, c2 = _find_card_lines(img)
        if q2 is not None and (quad is None or touches or c2 > find_conf):
            quad, find_conf = q2, max(c2, 0.3)
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
