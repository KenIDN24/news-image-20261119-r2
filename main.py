#!/usr/bin/env python3
"""The Day in World News — Thursday 19 November 2026, in one image.

Round 2 design: thirteen stories, every one of them drawn.  A stat
strip under the masthead carries the day's six biggest numbers at a
size that survives shrinking; three lead panels carry real scenes;
nine grid cards each pair an icon with a headline and one fact; a
culture banner closes the page.  Nothing on the page is set smaller
than a phone thumbnail can read.

The program is pure standard library.  It loads pre-extracted glyph
contours from fonts.bin (see gen_fonts.py), rasterises them itself,
draws every picture with its own geometry, and writes a deterministic
PNG to /output/entry.png (or $MAKEFUN_OUTPUT_DIR/entry.png).
"""
import json
import os
import struct
import sys
import zipfile
import zlib

PAGE = 1024
SS = 3  # supersampling factor for glyph rasterisation

PAPER = (250, 247, 240)
INK = (22, 24, 28)
FAINT = (105, 102, 96)
RULE = (178, 170, 156)
WHITE = (255, 255, 255)
RED = (178, 34, 43)
NAVY = (27, 58, 110)
TEAL = (10, 110, 108)
GOLD = (176, 124, 12)
SKY = (196, 216, 228)
SAND = (233, 220, 183)


# ---------------------------------------------------------------- PNG out

def save_png(canvas, path):
    """Write the RGB canvas as a PNG with fixed chunking (deterministic)."""
    raw = bytearray()
    stride = PAGE * 3
    for y in range(PAGE):
        raw.append(0)
        raw += canvas[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", PAGE, PAGE, 8, 2, 0, 0, 0)

    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(
            ">I", zlib.crc32(body) & 0xFFFFFFFF)

    blob = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)
    return len(blob)


# ---------------------------------------------------------------- canvas

class Canvas:
    def __init__(self):
        self.px = bytearray(PAGE * PAGE * 3)
        self.fill(0, 0, PAGE, PAGE, PAPER)

    def _put(self, x, y, colour):
        if 0 <= x < PAGE and 0 <= y < PAGE:
            i = (y * PAGE + x) * 3
            self.px[i:i + 3] = bytes(colour)

    def _blend(self, x, y, colour, cover):
        """cover is 0..SS*SS subpixel hits for this pixel; neighbouring
        spans can share a boundary subpixel, so clamp the count."""
        cover = min(cover, SS * SS)
        if cover <= 0 or not (0 <= x < PAGE and 0 <= y < PAGE):
            return
        i = (y * PAGE + x) * 3
        keep = SS * SS - cover
        for ch in range(3):
            self.px[i + ch] = (self.px[i + ch] * keep + colour[ch] * cover) // (SS * SS)

    def fill(self, x, y, w, h, colour):
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(PAGE, x + w), min(PAGE, y + h)
        if x1 <= x0 or y1 <= y0:
            return
        row = bytes(colour) * (x1 - x0)
        for yy in range(y0, y1):
            i = (yy * PAGE + x0) * 3
            self.px[i:i + len(row)] = row

    def frame(self, x, y, w, h, colour, t=1):
        self.fill(x, y, w, t, colour)
        self.fill(x, y + h - t, w, t, colour)
        self.fill(x, y, t, h, colour)
        self.fill(x + w - t, y, t, h, colour)

    def line(self, x0, y0, x1, y1, colour, t=1):
        """Thick segment, stamped along its length."""
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        r = t // 2
        for s in range(steps + 1):
            cx = x0 + (x1 - x0) * s // steps
            cy = y0 + (y1 - y0) * s // steps
            self.fill(cx - r, cy - r, t, t, colour)

    def dash_line(self, x0, y0, x1, y1, colour, t=2, dash=7, gap=5):
        steps = max(abs(x1 - x0), abs(y1 - y0), 1)
        r = t // 2
        for s in range(steps + 1):
            if (s % (dash + gap)) >= dash:
                continue
            cx = x0 + (x1 - x0) * s // steps
            cy = y0 + (y1 - y0) * s // steps
            self.fill(cx - r, cy - r, t, t, colour)

    def poly(self, pts, colour):
        """Filled polygon via scanline spans (1x; used for chunky art)."""
        ys = [p[1] for p in pts]
        for yy in range(max(0, min(ys)), min(PAGE, max(ys) + 1)):
            hits = []
            n = len(pts)
            for i in range(n):
                xa, ya = pts[i]
                xb, yb = pts[(i + 1) % n]
                if ya == yb:
                    continue
                if (ya <= yy < yb) or (yb <= yy < ya):
                    hits.append(xa + (yy - ya) * (xb - xa) // (yb - ya))
            hits.sort()
            for i in range(0, len(hits) - 1, 2):
                self.fill(hits[i], yy, hits[i + 1] - hits[i], 1, colour)

    def circle(self, cx, cy, r, colour, ring=0):
        for yy in range(cy - r, cy + r + 1):
            dy = yy - cy
            span = int((r * r - dy * dy) ** 0.5) if r * r >= dy * dy else -1
            if span < 0:
                continue
            if ring:
                inner2 = (r - ring) * (r - ring) - dy * dy
                if inner2 >= 0:
                    ispan = int(inner2 ** 0.5)
                    self.fill(cx - span, yy, span - ispan, 1, colour)
                    self.fill(cx + ispan, yy, span - ispan, 1, colour)
                else:
                    self.fill(cx - span, yy, 2 * span, 1, colour)
            else:
                self.fill(cx - span, yy, 2 * span, 1, colour)


# ---------------------------------------------------------------- fonts

class Face:
    """One packed face from fonts.bin: advances + point contours."""

    def __init__(self, payload):
        doc = json.loads(payload)
        self.upem = doc["upem"]
        self.glyphs = doc["glyphs"]
        self._edges = {}

    def advance(self, ch, size):
        g = self.glyphs.get(ch) or self.glyphs["?"]
        return g["a"] * size // self.upem

    def edges(self, ch):
        """Flatten a glyph's quadratic contours into straight edges,
        in font units, y up.  Cached: each character flattens once."""
        if ch in self._edges:
            return self._edges[ch]
        g = self.glyphs.get(ch) or self.glyphs["?"]
        out = []
        for contour in g["c"]:
            pts = [(contour[i], contour[i + 1], contour[i + 2])
                   for i in range(0, len(contour), 3)]
            if len(pts) < 2:
                continue
            # Walk the contour, inserting implied on-curve midpoints
            # between consecutive off-curve points, then flatten each
            # quadratic segment into 6 straight steps.
            seq = []
            n = len(pts)
            for i in range(n):
                p = pts[i]
                q = pts[(i + 1) % n]
                seq.append(p)
                if not p[2] and not q[2]:
                    seq.append(((p[0] + q[0]) // 2, (p[1] + q[1]) // 2, 1))
            if seq[0][2]:
                start = seq[0]
                walk = seq[1:] + [seq[0]]
            else:
                start = seq[-1]
                walk = seq[:-1]
            cx, cy = start[0], start[1]
            i = 0
            while i < len(walk):
                p = walk[i]
                if p[2]:
                    out.append((cx, cy, p[0], p[1]))
                    cx, cy = p[0], p[1]
                    i += 1
                else:
                    q = walk[(i + 1) % len(walk)]
                    for s in range(1, 7):
                        t = s / 6.0
                        nx = (1 - t) ** 2 * cx + 2 * (1 - t) * t * p[0] + t * t * q[0]
                        ny = (1 - t) ** 2 * cy + 2 * (1 - t) * t * p[1] + t * t * q[1]
                        out.append((cx, cy, nx, ny))
                        cx, cy = nx, ny
                    i += 2
            # close back to the start point if the walk ended elsewhere
            if (cx, cy) != (start[0], start[1]):
                out.append((cx, cy, start[0], start[1]))
        self._edges[ch] = out
        return out


FACES = {}


def load_faces(here):
    with zipfile.ZipFile(os.path.join(here, "fonts.bin")) as z:
        for key in ("regular", "bold"):
            FACES[key] = Face(z.read(key + ".json"))


def measure(face, size, text, tracking=0):
    f = FACES[face]
    w = sum(f.advance(ch, size) for ch in text)
    if tracking and len(text) > 1:
        w += tracking * (len(text) - 1)
    return w


def draw_text(cv, face, size, x, y_top, text, colour, tracking=0):
    """Draw text with its baseline sitting size*0.8 below y_top
    (Liberation's ascender share); returns the x after the last glyph."""
    f = FACES[face]
    scale = size / f.upem
    base = y_top + int(size * 0.8)
    pen = x
    for ch in text:
        edges = f.edges(ch)
        if edges:
            _stamp_glyph(cv, edges, pen, base, scale, colour)
        pen += f.advance(ch, size) + tracking
    return pen


def _stamp_glyph(cv, edges, pen_x, base_y, scale, colour):
    """Rasterise one glyph: transform edges to subpixel space, then
    fill spans row by row with a non-zero winding count."""
    sub = []
    xs, ys = [], []
    for x0, y0, x1, y1 in edges:
        ax = pen_x * SS + int(round(x0 * scale * SS))
        ay = base_y * SS - int(round(y0 * scale * SS))
        bx = pen_x * SS + int(round(x1 * scale * SS))
        by = base_y * SS - int(round(y1 * scale * SS))
        sub.append((ax, ay, bx, by))
        xs += [ax, bx]
        ys += [ay, by]
    if not sub:
        return
    top, bottom = min(ys), max(ys)
    left, right = min(xs), max(xs)
    hits = {}
    for yy in range(top, bottom):
        spans = []
        for ax, ay, bx, by in sub:
            if ay == by:
                continue
            if (ay <= yy < by) or (by <= yy < ay):
                xi = ax + (yy - ay) * (bx - ax) / (by - ay)
                spans.append((xi, 1 if by > ay else -1))
        if not spans:
            continue
        spans.sort()
        wind = 0
        prev = None
        for xi, delta in spans:
            if wind != 0 and prev is not None and xi > prev:
                hits.setdefault(yy, []).append((prev, xi))
            wind += delta
            prev = xi
    cover = {}
    for yy, spans in hits.items():
        py = yy // SS
        if not (0 <= py < PAGE):
            continue
        for x_from, x_to in spans:
            xa = max(int(x_from), left)
            xb = min(int(round(x_to)), right)
            for xx in range(xa, xb):
                key = (xx // SS, py)
                cover[key] = cover.get(key, 0) + 1
    for (px, py), count in cover.items():
        cv._blend(px, py, colour, count)


def wrap(face, size, text, width, tracking=0):
    """Greedy word wrap; returns a list of lines that fit width."""
    lines, cur = [], ""
    for word in text.split():
        trial = word if not cur else cur + " " + word
        if measure(face, size, trial, tracking) <= width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- lead art

def art_shenzhen(cv, x, y, w, h):
    """Skyline under a red sun, three flag chips for the three powers."""
    cv.fill(x, y, w, h, SKY)
    cv.circle(x + w - 52, y + 30, 17, RED)
    heights = [38, 62, 48, 78, 56, 88, 66, 44, 72, 52, 82, 60, 40, 68]
    bw = w // len(heights)
    for i, bh in enumerate(heights):
        bx = x + i * bw + 2
        by = y + h - bh
        cv.fill(bx, by, bw - 4, bh, NAVY if i % 3 else TEAL)
        for wy in range(by + 7, y + h - 6, 11):
            for wx in range(bx + 4, bx + bw - 7, 9):
                if (wx * 7 + wy * 13 + i) % 3:
                    cv.fill(wx, wy, 4, 5, (240, 226, 160))
    cv.fill(x, y + h - 4, w, 4, INK)
    # flag chips: US stripes + canton, CN red with stars, RU tricolour
    chips = [(x + 10, "US"), (x + 62, "CN"), (x + 114, "RU")]
    for cx, tag in chips:
        cy = y + 8
        cv.frame(cx, cy, 44, 28, INK, 1)
        if tag == "US":
            for r in range(4):
                cv.fill(cx + 1, cy + 1 + r * 7, 42, 3, RED if r % 2 == 0 else WHITE)
            cv.fill(cx + 1, cy + 1, 18, 13, NAVY)
        elif tag == "CN":
            cv.fill(cx + 1, cy + 1, 42, 26, RED)
            cv.circle(cx + 9, cy + 8, 3, (245, 200, 60))
            cv.circle(cx + 17, cy + 5, 1, (245, 200, 60))
            cv.circle(cx + 18, cy + 11, 1, (245, 200, 60))
        else:
            cv.fill(cx + 1, cy + 1, 42, 8, WHITE)
            cv.fill(cx + 1, cy + 10, 42, 8, NAVY)
            cv.fill(cx + 1, cy + 19, 42, 8, RED)


def art_gaza(cv, x, y, w, h):
    """A tank on sand, a dashed ceasefire line, the sea at the edge."""
    cv.fill(x, y, w, h, SAND)
    cv.fill(x, y, 26, h, (168, 196, 205))
    line_x = x + w - 74
    cv.dash_line(line_x, y + 6, line_x, y + h - 8, GOLD, t=3, dash=8, gap=6)
    draw_text(cv, "bold", 12, line_x - 92, y + 8, "YELLOW LINE", (146, 104, 8))
    # tank: tracks, hull, turret, barrel, all in ink silhouette
    tx, ty = x + 66, y + h - 46
    cv.fill(tx, ty + 22, 118, 16, INK)
    for i in range(6):
        cv.circle(tx + 12 + i * 19, ty + 30, 6, PAPER)
    cv.poly([(tx + 8, ty + 22), (tx + 20, ty + 8), (tx + 104, ty + 8),
             (tx + 114, ty + 22)], INK)
    cv.fill(tx + 44, ty - 6, 40, 14, INK)
    cv.fill(tx + 84, ty - 3, 52, 5, INK)
    cv.circle(x + w - 30, y + 22, 9, RED, ring=3)
    cv.circle(x + w - 30, y + 22, 2, RED)


def art_speaker(cv, x, y, w, h):
    """House bars: Democrats 259, Republicans 176, 218 to win."""
    cv.fill(x, y, w, h, (240, 238, 232))
    rows = [("DEM", 259, NAVY, 30), ("REP", 176, RED, 74)]
    maxv = 280
    win_x = x + 52 + (w - 130) * 218 // maxv
    cv.fill(win_x, y + 8, 2, h - 30, INK)
    draw_text(cv, "bold", 12, win_x - 24, y + h - 20, "218 TO WIN", INK)
    for label, val, colour, dy in rows:
        draw_text(cv, "bold", 15, x + 10, y + dy - 16, label, colour)
        bw = (w - 130) * val // maxv
        cv.fill(x + 52, y + dy - 14, bw, 20, colour)
        num = str(val)
        draw_text(cv, "bold", 26, x + w - 8 - measure("bold", 26, num),
                  y + dy - 20, num, colour)


# ---------------------------------------------------------------- icons

def icon_chip(cv, x, y, s, colour):
    cv.frame(x + s // 4, y + s // 4, s // 2, s // 2, colour, 4)
    cv.frame(x + s // 4 + 7, y + s // 4 + 7, s // 2 - 14, s // 2 - 14, colour, 3)
    for i in range(3):
        p = x + s // 4 + 6 + i * (s // 2 - 12) // 2
        cv.fill(p, y + 2, 4, s // 4 - 2, colour)
        cv.fill(p, y + 3 * s // 4, 4, s // 4 - 2, colour)
        cv.fill(x + 2, y + s // 4 + 6 + i * (s // 2 - 12) // 2, s // 4 - 2, 4, colour)
        cv.fill(x + 3 * s // 4, y + s // 4 + 6 + i * (s // 2 - 12) // 2, s // 4 - 2, 4, colour)


def icon_bridge(cv, x, y, s, colour):
    deck = y + s // 2 + 2
    for tx in (x + 7, x + s - 12):
        cv.fill(tx, y + 5, 5, s - 12, colour)
        cv.fill(tx - 2, y + 5, 9, 3, colour)
    cv.fill(x + 2, deck, s - 4, 4, colour)
    # the dropped span: a paper gap mid-deck, two ends tilting down
    cv.fill(x + s // 2 - 6, deck, 12, 4, PAPER)
    cv.line(x + s // 2 - 6, deck + 1, x + s // 2 - 2, deck + 7, colour, 3)
    cv.line(x + s // 2 + 6, deck + 1, x + s // 2 + 2, deck + 7, colour, 3)
    for i in range(3):
        wx = x + 5 + i * (s // 3)
        cv.line(wx, y + s - 5, wx + 8, y + s - 5, colour, 2)


def icon_chart_down(cv, x, y, s, colour):
    cv.fill(x + 3, y + 4, 3, s - 10, colour)
    cv.fill(x + 3, y + s - 7, s - 8, 3, colour)
    pts = [(x + 9, y + 11), (x + s // 2 - 4, y + 20), (x + s - 13, y + s - 15)]
    for a, b in zip(pts, pts[1:]):
        cv.line(a[0], a[1], b[0], b[1], colour, 4)
    ex, ey = pts[-1]
    cv.poly([(ex + 3, ey + 3), (ex - 8, ey + 1), (ex + 1, ey - 8)], colour)


def icon_probe(cv, x, y, s, colour):
    # a dish as a crescent band: outer and inner arcs joined into one
    # filled polygon, a feed mast, and signal dots climbing away
    import math
    cx, cy = x + 16, y + s - 15
    pts = []
    for a in range(60, 301, 12):
        rad = a * 3.14159 / 180.0
        pts.append((cx + int(13 * math.cos(rad)), cy + int(13 * math.sin(rad))))
    for a in range(300, 59, -12):
        rad = a * 3.14159 / 180.0
        pts.append((cx + int(7 * math.cos(rad)), cy + int(7 * math.sin(rad))))
    cv.poly(pts, colour)
    cv.line(cx, cy, cx + 13, cy - 13, colour, 3)
    cv.circle(cx + 14, cy - 14, 3, colour)
    cv.fill(cx - 2, cy + 2, 4, 8, colour)
    for d in (20, 27, 34):
        cv.circle(cx + d, cy - d, 2, colour)


def icon_flower(cv, x, y, s, colour):
    cx, cy = x + s // 2, y + s // 3
    for dx, dy in ((0, -11), (10, -6), (10, 6), (0, 11), (-10, 6), (-10, -6)):
        cv.circle(cx + dx, cy + dy, 7, colour)
    cv.circle(cx, cy, 5, GOLD)
    cv.line(cx, cy + 12, cx - 2, y + s - 4, TEAL, 3)
    cv.poly([(cx - 2, y + s - 14), (cx - 13, y + s - 18), (cx - 3, y + s - 6)], TEAL)


def icon_leaf(cv, x, y, s, colour):
    pts = []
    for i in range(21):
        t = i / 20.0
        pts.append((x + 6 + int(t * (s - 14)), y + s - 8 - int(t * (s - 16))
                    - int(10 * (t - t * t) * 2)))
    for a, b in zip(pts, pts[1:]):
        cv.line(a[0], a[1], b[0], b[1], colour, 3)
    cv.line(x + 6, y + s - 8, x + s - 8, y + 6, colour, 2)
    cv.poly([(x + 8, y + s - 6), (x + s - 6, y + 8), (x + s - 18, y + s - 16)], colour)


def icon_crowd(cv, x, y, s, colour):
    for cx, hr in ((x + 13, 7), (x + 30, 8)):
        cv.circle(cx, y + 11, hr, colour)
        cv.poly([(cx - hr - 4, y + s - 3), (cx - hr - 1, y + 21),
                 (cx + hr + 1, y + 21), (cx + hr + 4, y + s - 3)], colour)
    cv.fill(x + s - 5, y + 3, 3, 18, colour)
    cv.fill(x + s - 15, y + 3, 12, 8, RED)


def icon_cyclone(cv, x, y, s, colour):
    import math
    cx, cy = x + s // 2, y + s // 2
    for i in range(90):
        t = i / 89.0
        ang = t * 5.2
        r = 3 + t * (s // 2 - 5)
        px = cx + int(r * math.cos(ang))
        py = cy + int(r * math.sin(ang))
        cv.fill(px - 2, py - 2, 4, 4, colour)
    cv.circle(cx, cy, 3, colour)


def icon_shield(cv, x, y, s, colour):
    pts = [(x + s // 2, y + 3), (x + s - 6, y + 9), (x + s - 6, y + s // 2),
           (x + s // 2, y + s - 3), (x + 6, y + s // 2), (x + 6, y + 9)]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        cv.line(a[0], a[1], b[0], b[1], colour, 4)
    cx, cy = x + s // 2, y + s // 2 - 2
    for dx, dy in ((0, -8), (7, 5), (-7, 5)):
        cv.circle(cx + dx, cy + dy, 3, colour)


def icon_palm(cv, x, y, s, colour):
    cv.line(x + s // 2, y + s - 2, x + s // 2 + 4, y + 14, colour, 4)
    import math
    for a in (-160, -115, -70, -25, 15):
        rad = a * 3.14159 / 180.0
        ex = x + s // 2 + 4 + int(17 * math.cos(rad))
        ey = y + 14 + int(12 * math.sin(rad))
        cv.line(x + s // 2 + 4, y + 14, ex, ey + 5, colour, 3)


# ---------------------------------------------------------------- layout

MARGIN = 28
WIDTH = PAGE - 2 * MARGIN


def fit_size(face, size, text, max_width, tracking=0):
    while size > 10 and measure(face, size, text, tracking) > max_width:
        size -= 1
    return size


def text_block(cv, face, size, x, y, text, colour, width, line_h, max_lines=3):
    lines = wrap(face, size, text, width)
    for i, line in enumerate(lines[:max_lines]):
        draw_text(cv, face, size, x, y + i * line_h, line, colour)
    return y + min(len(lines), max_lines) * line_h


def masthead(cv):
    draw_text(cv, "bold", 17, MARGIN, 16, "THE DAY IN WORLD NEWS", INK, tracking=3)
    right = "13 STORIES · 3 CONTINENTS · ONE DAY"
    draw_text(cv, "bold", 15, PAGE - MARGIN - measure("bold", 15, right, 2),
              17, right, RED, tracking=2)
    cv.fill(MARGIN, 46, WIDTH, 3, INK)
    size = fit_size("bold", 84, "Thursday 19 November 2026", WIDTH)
    draw_text(cv, "bold", size, MARGIN, 56, "Thursday 19 November 2026", INK)


STATS = [
    ("$81.3bn", "LARGEST IPO\nEVER RAISED", NAVY),
    ("251–11", "JEFFRIES VOTE\nFOR SPEAKER", RED),
    ("24 hrs", "VOYAGER 1 SIGNAL\nTIME FROM EARTH", TEAL),
    ("$190.4m", "VAN GOGH RECORD\nAT SOTHEBY'S", GOLD),
    ("-561", "DOW POINTS LOST\nIN ONE SESSION", RED),
    ("259", "HOUSE SEATS HELD\nBY DEMOCRATS", NAVY),
]


def stat_strip(cv, y):
    cv.fill(MARGIN, y, WIDTH, 60, (240, 236, 226))
    cv.fill(MARGIN, y, WIDTH, 2, INK)
    cv.fill(MARGIN, y + 58, WIDTH, 2, INK)
    col = WIDTH // len(STATS)
    for i, (num, label, colour) in enumerate(STATS):
        x = MARGIN + i * col + 12
        draw_text(cv, "bold", 28, x, y + 5, num, colour)
        for j, part in enumerate(label.split("\n")):
            draw_text(cv, "bold", 10, x + 1, y + 37 + j * 11, part, FAINT, tracking=1)
        if i:
            cv.fill(MARGIN + i * col, y + 7, 1, 44, RULE)


LEADS = [
    (art_shenzhen, RED, "ASIA · DIPLOMACY",
     "Trump, Xi and Putin meet in Shenzhen",
     "First three-way summit of the three powers, at APEC: nuclear arms, Iran and trade."),
    (art_gaza, GOLD, "MIDDLE EAST · GAZA",
     "Israeli tanks cross Gaza ceasefire line",
     "First ground advance past the Yellow Line since the 2025 truce; families flee Nuseirat."),
    (art_speaker, NAVY, "AMERICAS · US POLITICS",
     "Jeffries nominated for Speaker, 251–11",
     "Hakeem Jeffries is poised to become the first Black Speaker of the House."),
]


def lead_panel(cv, x, y, w, h, art, colour, kicker, head, fact):
    cv.frame(x, y, w, h, RULE, 1)
    cv.fill(x, y, w, 6, colour)
    art(cv, x + 8, y + 14, w - 16, 112)
    ky = y + 136
    draw_text(cv, "bold", 13, x + 12, ky, kicker, colour, tracking=1)
    hy = text_block(cv, "bold", 26, x + 12, ky + 22, head, INK, w - 24, 30, 3)
    text_block(cv, "regular", 15, x + 12, hy + 4, fact, (58, 56, 52), w - 24, 19, 3)


CARDS = [
    (icon_chip, NAVY, "BUSINESS · AI",
     "Anthropic IPO is biggest ever: $81.3bn",
     "Priced at $83 a share; the Claude maker is valued near $1.5tn."),
    (icon_bridge, RED, "EUROPE · UKRAINE WAR",
     "Crimea bridge hit again; Odesa toll rises",
     "Kerch traffic halted; Russian strikes on Odesa kill four."),
    (icon_chart_down, RED, "MARKETS · US",
     "Dow falls 561 as Fed hints at hike",
     "Fifth straight fall for the S&P; Nvidia beats, yet slips 2.9%."),
    (icon_probe, TEAL, "SCIENCE · SPACE",
     "Voyager 1 passes one light-day",
     "16.1bn miles out; its signals now take a full 24 hours."),
    (icon_flower, GOLD, "CULTURE · ART",
     "Van Gogh sells for record $190.4m",
     "Chestnut Trees in Flower tops the old $117m record."),
    (icon_leaf, TEAL, "CLIMATE · COP31",
     "COP31 deadlocked with two days left",
     "21 issues still open in Antalya; OPEC defends fossil fuels."),
    (icon_crowd, NAVY, "EUROPE · PROTEST",
     "Prague fills streets for free media",
     "Hundreds of thousands defend media independence."),
    (icon_cyclone, TEAL, "WEATHER · INDIA",
     "Cyclone Akvan nears India coast",
     "Landfall is seen between Machilipatnam and Puri."),
    (icon_shield, NAVY, "ASIA · DEFENCE",
     "India, Japan open 2+2 talks in Tokyo",
     "Defence and security ties top the ministers' agenda."),
]


def tint_of(colour):
    return tuple(int(c + (p - c) * 0.86) for c, p in zip(colour, PAPER))


def card(cv, x, y, w, h, icon, colour, kicker, head, fact):
    cv.fill(x, y, w, 3, colour)
    cv.frame(x, y, w, h, RULE, 1)
    cv.fill(x + 1, y + 3, w - 2, 50, tint_of(colour))
    icon(cv, x + 10, y + 10, 42, colour)
    draw_text(cv, "bold", 12, x + 62, y + 24, kicker, colour, tracking=1)
    hy = text_block(cv, "bold", 20, x + 12, y + 56, head, INK, w - 24, 23, 2)
    text_block(cv, "regular", 13, x + 12, hy + 3, fact, (58, 56, 52), w - 24, 16, 2)


def banner(cv, y):
    h = 54
    cv.frame(MARGIN, y, WIDTH, h, RULE, 1)
    cv.fill(MARGIN, y, 6, h, GOLD)
    icon_palm(cv, MARGIN + 16, y + 5, 44, TEAL)
    draw_text(cv, "bold", 23, MARGIN + 74, y + 8, "GTA VI launch day stuns players", INK)
    draw_text(cv, "regular", 14, MARGIN + 74, y + 34,
              "Worldwide rollout reaches every timezone; launch-day queues wrap blocks.", FAINT)
    right = "CULTURE · GAMING"
    draw_text(cv, "bold", 12, PAGE - MARGIN - measure("bold", 12, right, 1) - 12,
              y + 10, right, GOLD, tracking=1)


def build_page():
    cv = Canvas()
    masthead(cv)
    stat_strip(cv, 140)
    lead_y, lead_h, gap = 212, 272, 12
    lead_w = (WIDTH - 2 * gap) // 3
    for i, (art, colour, kicker, head, fact) in enumerate(LEADS):
        lead_panel(cv, MARGIN + i * (lead_w + gap), lead_y, lead_w, lead_h,
                   art, colour, kicker, head, fact)
    grid_y = lead_y + lead_h + 10
    card_w, card_h, cgap = lead_w, 138, gap
    for i, (icon, colour, kicker, head, fact) in enumerate(CARDS):
        r, c = divmod(i, 3)
        card(cv, MARGIN + c * (card_w + cgap), grid_y + r * (card_h + 9),
             card_w, card_h, icon, colour, kicker, head, fact)
    banner(cv, grid_y + 3 * card_h + 2 * 9 + 10)
    fy = PAGE - 22
    cv.fill(MARGIN, fy - 7, WIDTH, 1, RULE)
    draw_text(cv, "regular", 11, MARGIN, fy,
              "Every story was reported on Thursday 19 November 2026", FAINT)
    src = "Sources: Reuters · AP · BBC · Al Jazeera · CNN · UN News · NASA · Sotheby's"
    draw_text(cv, "regular", 11, PAGE - MARGIN - measure("regular", 11, src),
              fy, src, FAINT)
    return cv


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    load_faces(here)
    page = build_page()
    out_dir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(out_dir, exist_ok=True)
    size = save_png(page.px, os.path.join(out_dir, "entry.png"))
    print("entry.png:", size, "bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
