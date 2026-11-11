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
import itertools

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


# ── Card outline v2 ──
def _grad(img):
    g = cv2.GaussianBlur(img, (0, 0), 1.5).astype(np.float32)
    gx = np.max(np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)), axis=2)
    gy = np.max(np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)), axis=2)
    return gx, gy

class Scorer:
    def __init__(self, img):
        self.h, self.w = img.shape[:2]
        self.gx, self.gy = _grad(img)
        self.norm = float(np.percentile(np.hypot(self.gx, self.gy), 75)) + 1e-3

    def _along(self, a, b, off, n=60):
        """|gradient across the side| at points along a->b, shifted `off` px along the inward normal."""
        d = b - a; L = np.hypot(*d) + 1e-9; t = d / L; nrm = np.array([-t[1], t[0]])
        u = np.linspace(.1, .9, n)[:, None]
        P = a + u * d + off * nrm
        best = np.zeros(n, np.float32)
        for dd in (-1.5, 0, 1.5):  # allow the edge to sit a pixel or so off the line
            Q = (P + dd * nrm).astype(np.float32)
            gx = cv2.remap(self.gx, Q[:, 0:1], Q[:, 1:2], cv2.INTER_LINEAR).ravel()
            gy = cv2.remap(self.gy, Q[:, 0:1], Q[:, 1:2], cv2.INTER_LINEAR).ravel()
            best = np.maximum(best, np.abs(gx * nrm[0]) + np.abs(gy * nrm[1]))
        return best

    def side(self, a, b, center):
        d = b - a; L = np.hypot(*d) + 1e-9; nrm = np.array([-d[1], d[0]]) / L
        if np.dot(center - (a + b) / 2, nrm) < 0: nrm = -nrm  # point inward
        a2, b2 = a, b
        def at(off):
            dd = b2 - a2; t = dd / (np.hypot(*dd) + 1e-9); n_ = np.array([-t[1], t[0]])
            sgn = 1 if np.dot(n_, nrm) > 0 else -1
            return self._along(a2, b2, off * sgn)
        on = np.percentile(at(0), 55)
        gap = max(4.0, .02 * min(self.w, self.h))
        beside = min(np.percentile(at(gap), 55), np.percentile(at(-gap), 55))  # texture: busy on both sides
        return (on - 0.7 * beside) / self.norm

    def score(self, q):
        q = np.asarray(q, np.float32)
        cw = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
        ch = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
        if min(cw, ch) < .12 * min(self.w, self.h): return 0, None
        r = max(cw, ch) / min(cw, ch)
        fit = float(np.exp(-((r - 1.4) / .05) ** 2))
        if fit < .05: return 0, None
        # opposite sides should be near parallel (mild perspective only)
        c = q.mean(0)
        sides = [self.side(q[k], q[(k + 1) % 4], c) for k in range(4)]
        s = np.clip(sides, 0, None)
        if s.min() < 2.0: return 0, None
        area = cw * ch / (self.w * self.h)
        return fit * float(np.prod(np.minimum(s, 12)) ** .25) * area ** .75, sides

def refine(sc, q, span=.025, steps=13):
    """Move each side in/out to sit exactly on the strongest edge: a coarse search, then a
    fine one around it (about a third of a pixel), so borders are measured accurately."""
    q = np.asarray(q, np.float32).copy()
    dim = min(sc.w, sc.h)
    for sp, n in ((span, steps), (2.5 / dim, 21), (.8 / dim, 17)):
        c = q.mean(0)
        sides = []
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            d = b - a; nrm = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-9)
            if np.dot(c - (a + b) / 2, nrm) < 0: nrm = -nrm
            offs = np.linspace(-sp, sp, n) * dim
            v = np.array([np.mean(sc._along(a + o * nrm, b + o * nrm, 0)) for o in offs])
            i = int(np.argmax(v)); o = offs[i]
            if 0 < i < n - 1:  # parabola through the peak for a sub-step position
                den = v[i - 1] - 2 * v[i] + v[i + 1]
                if den < 0: o += 0.5 * (v[i - 1] - v[i + 1]) / den * (offs[1] - offs[0])
            sides.append((a + o * nrm, b + o * nrm))
        e = _corners(sides)
        if e is None: break
        q = e
    return q


def candidates(img):
    h, w = img.shape[:2]; out = []
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    bil = cv2.GaussianBlur(cv2.cvtColor(cv2.bilateralFilter(img, 9, 60, 9), cv2.COLOR_BGR2GRAY), (5, 5), 0)
    for src in (gray, bil):
        for lo, hi in ((15, 50), (30, 90), (60, 160)):
            e0 = cv2.Canny(src, lo, hi)
            for it in (1, 2):
                e = cv2.dilate(e0, np.ones((3, 3), np.uint8), iterations=it)
                cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
                for c in cnts:
                    if cv2.contourArea(c) < .03 * w * h: continue
                    peri = cv2.arcLength(c, True)
                    for eps in (.02, .04):
                        ap = cv2.approxPolyDP(c, eps * peri, True)
                        if len(ap) == 4 and cv2.isContourConvex(ap):
                            out.append(_order(ap.reshape(4, 2).astype(np.float32)))
                    out.append(_order(cv2.boxPoints(cv2.minAreaRect(c)).astype(np.float32)))
    return out

def _shift_side(q, k, off):
    q = q.copy(); c = q.mean(0); a, b = q[k], q[(k + 1) % 4]
    d = b - a; nrm = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-9)
    if np.dot(c - (a + b) / 2, nrm) < 0: nrm = -nrm
    q[k] = a - off * nrm; q[(k + 1) % 4] = b - off * nrm   # + off = outward
    return q

def _corners(q_sides):
    out = []
    for k in range(4):
        l1, l2 = q_sides[(k - 1) % 4], q_sides[k]
        p = _intersect((*l1[0], *l1[1]), (*l2[0], *l2[1]))
        out.append(p)
    return None if any(p is None for p in out) else np.float32(out)

def expand(sc, q):
    """The outline found may be a frame line inside the border. Look outward on each side
    for the next straight edge; keep the expansion that still makes a card-shaped outline."""
    q = np.asarray(q, np.float32); base, sides = sc.score(q)
    if not base: return q
    dim = min(sc.w, sc.h); c = q.mean(0)
    opts = []
    for k in range(4):
        a, b = q[k], q[(k + 1) % 4]
        d = b - a; nrm = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-9)
        if np.dot(c - (a + b) / 2, nrm) < 0: nrm = -nrm
        offs = np.arange(.006, .12, .002) * dim
        v = np.array([np.percentile(sc._along(a - o * nrm, b - o * nrm, 0), 55) for o in offs])
        pk = [i for i in range(1, len(v) - 1) if v[i] >= v[i - 1] and v[i] >= v[i + 1] and v[i] > .35 * sc.norm]
        opts.append([0.0] + [float(offs[i]) for i in pk[:2]])
    best, bs = q, base * 1.0
    for combo in itertools.product(*opts):
        if not any(combo): continue
        sides = []
        for k in range(4):
            a, b = q[k], q[(k + 1) % 4]
            d = b - a; nrm = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-9)
            if np.dot(c - (a + b) / 2, nrm) < 0: nrm = -nrm
            sides.append((a - combo[k] * nrm, b - combo[k] * nrm))
        e = _corners(sides)
        if e is None: continue
        s2, sd2 = sc.score(e)
        if not s2 or min(sd2) < 2.5: continue
        cw = (np.linalg.norm(e[1] - e[0]) + np.linalg.norm(e[2] - e[3])) / 2
        ch = (np.linalg.norm(e[3] - e[0]) + np.linalg.norm(e[2] - e[1])) / 2
        r = max(cw, ch) / min(cw, ch)
        if abs(r - 1.4) > .05: continue
        # bigger outline that still looks like a card wins unless clearly weaker
        if cv2.contourArea(e) > cv2.contourArea(best):
            best, bs = e, s2
    return best

def _find_card_v2(img):
    """Card outline v2: many candidate outlines (contours, straight edges, the photo frame),
    each snapped to the strongest nearby edge and scored on card shape (2.5x3.5), four clean
    straight sides and size; then widened if a frame line inside the border was found."""
    sc = Scorer(img)
    cands = candidates(img)
    q2, _ = _find_card_lines(img)
    if q2 is not None: cands.append(q2)
    # card filling most of the photo: start from the photo's own frame and snap inward
    for inset in (.01, .04):
        fr = np.float32([[inset * sc.w, inset * sc.h], [(1 - inset) * sc.w, inset * sc.h],
                         [(1 - inset) * sc.w, (1 - inset) * sc.h], [inset * sc.w, (1 - inset) * sc.h]])
        cands.append(refine(sc, fr, span=.06, steps=31))
    # dedupe near-identical candidates
    uniq = []
    for q in cands:
        if all(np.abs(q - u).max() > .01 * max(sc.w, sc.h) for u in uniq): uniq.append(q)
    scored = []
    for q in uniq:
        s, _ = sc.score(q)
        if s > 0: scored.append((s, q))
    scored.sort(key=lambda t: -t[0])
    best, bs = None, 0
    for s, q in scored[:8]:          # refine the most promising, then rescore
        r = refine(sc, q)
        s2, _ = sc.score(r)
        if s2 > bs: best, bs = r, s2
    if best is not None:
        best = expand(sc, best)
    return best, bs


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

    q_v2, s_v2 = _find_card_v2(img)
    if q_v2 is not None:
        quad, find_conf = q_v2, float(min(1.0, 0.3 + s_v2 / 4))
    else:
        quad, find_conf = _find_card(img)
    hh, ww = img.shape[:2]
    touches = quad is not None and (quad[:, 0].min() < 3 or quad[:, 1].min() < 3 or
                                    quad[:, 0].max() > ww - 3 or quad[:, 1].max() > hh - 3)
    if q_v2 is None and (quad is None or touches):
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
    card_quad, method = quad, ("outline" if q_v2 is not None else "edge")
    grow = {k: 0 for k in "lrtb"}
    for a, b in (("l", "r"), ("t", "b")):
        if q_v2 is None and outer[a][1] and outer[b][1]:  # v2 already widens frame-line outlines
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
