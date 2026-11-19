#!/usr/bin/env python3
"""Extract the glyphs main.py needs out of the Liberation Sans TTFs and
pack them into fonts.bin.

Why not ship the TTFs?  A full face is ~400 KB; this page only ever
prints about a hundred distinct characters.  This script reads each
TrueType file once (cmap, glyf, loca, hmtx, head), resolves composite
glyphs into plain point contours, and stores, per character: its
advance width and its contours as [x, y, on_curve] triples in font
units.  The result is one ZIP (fixed timestamps, so rebuilds are
byte-identical) holding one JSON document per face.  main.py contains
the matching loader and a scanline rasteriser; it never parses TTF.
"""
import json
import os
import struct
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FACES = {
    "regular": "LiberationSans-Regular.ttf",
    "bold": "LiberationSans-Bold.ttf",
}
FIXED_DATE = (2026, 11, 19, 0, 0, 0)

# Every character the front page can print: printable ASCII plus the
# handful of typographic marks the headlines and kickers use.
CHARSET = [chr(c) for c in range(32, 127)] + [
    "–", "—", "·", "€", "£", "’", "‘", "“", "”", "…", "×", "−", "°", "✓",
]


def u16(buf, off):
    return struct.unpack_from(">H", buf, off)[0]


def i16(buf, off):
    return struct.unpack_from(">h", buf, off)[0]


def u32(buf, off):
    return struct.unpack_from(">I", buf, off)[0]


class Face:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        self.tables = {}
        count = u16(self.data, 4)
        for i in range(count):
            off = 12 + i * 16
            tag = self.data[off:off + 4].decode("latin1")
            self.tables[tag] = (u32(self.data, off + 8), u32(self.data, off + 12))
        head = self.tables["head"][0]
        self.upem = u16(self.data, head + 18)
        self.loc_format = i16(self.data, head + 50)
        hhea = self.tables["hhea"][0]
        self.n_hmetrics = u16(self.data, hhea + 34)
        maxp = self.tables["maxp"][0]
        self.n_glyphs = u16(self.data, maxp + 4)
        self._cmap = self._read_cmap()
        self._loca = self._read_loca()

    def _table(self, tag):
        off, length = self.tables[tag]
        return self.data[off:off + length]

    def _read_cmap(self):
        cmap = self._table("cmap")
        chosen = None
        n = u16(cmap, 2)
        for i in range(n):
            platform = u16(cmap, 4 + i * 8)
            encoding = u16(cmap, 6 + i * 8)
            sub_off = u32(cmap, 8 + i * 8)
            if u16(cmap, sub_off) == 4 and (platform, encoding) in ((3, 1), (0, 3), (0, 4)):
                chosen = sub_off
                break
            if u16(cmap, sub_off) == 4 and chosen is None:
                chosen = sub_off
        assert chosen is not None, "no format-4 cmap"
        seg_x2 = u16(cmap, chosen + 6)
        segs = seg_x2 // 2
        end_base = chosen + 14
        start_base = end_base + seg_x2 + 2
        delta_base = start_base + seg_x2
        range_base = delta_base + seg_x2
        mapping = {}
        for s in range(segs):
            end = u16(cmap, end_base + s * 2)
            start = u16(cmap, start_base + s * 2)
            delta = i16(cmap, delta_base + s * 2)
            roff = u16(cmap, range_base + s * 2)
            for ch in range(start, end + 1):
                if roff == 0:
                    gid = (ch + delta) & 0xFFFF
                else:
                    pos = range_base + s * 2 + roff + (ch - start) * 2
                    raw = u16(cmap, pos)
                    gid = (raw + delta) & 0xFFFF if raw else 0
                if gid:
                    mapping[ch] = gid
        return mapping

    def _read_loca(self):
        loca = self._table("loca")
        out = []
        if self.loc_format == 0:
            for i in range(self.n_glyphs + 1):
                out.append(u16(loca, i * 2) * 2)
        else:
            for i in range(self.n_glyphs + 1):
                out.append(u32(loca, i * 4))
        return out

    def advance(self, gid):
        hmtx = self.tables["hmtx"][0]
        idx = min(gid, self.n_hmetrics - 1)
        return u16(self.data, hmtx + idx * 4)

    def glyph_points(self, gid, depth=0):
        """Return a list of contours; each contour is a flat list
        [x0, y0, on0, x1, y1, on1, ...] in font units."""
        start, end = self._loca[gid], self._loca[gid + 1]
        if start == end or depth > 4:
            return []
        glyf_off = self.tables["glyf"][0]
        base = glyf_off + start
        n_contours = i16(self.data, base)
        if n_contours >= 0:
            ends = [u16(self.data, base + 10 + i * 2) for i in range(n_contours)]
            pos = base + 10 + n_contours * 2
            instr_len = u16(self.data, pos)
            pos += 2 + instr_len
            total = ends[-1] + 1 if ends else 0
            flags = []
            while len(flags) < total:
                flag = self.data[pos]
                pos += 1
                flags.append(flag)
                if flag & 0x08:
                    rep = self.data[pos]
                    pos += 1
                    flags.extend([flag] * rep)
            xs, x = [], 0
            for flag in flags:
                if flag & 0x02:
                    step = self.data[pos]
                    pos += 1
                    x += step if flag & 0x10 else -step
                elif not (flag & 0x10):
                    x += i16(self.data, pos)
                    pos += 2
                xs.append(x)
            ys, y = [], 0
            for flag in flags:
                if flag & 0x04:
                    step = self.data[pos]
                    pos += 1
                    y += step if flag & 0x20 else -step
                elif not (flag & 0x20):
                    y += i16(self.data, pos)
                    pos += 2
                ys.append(y)
            contours, prev = [], 0
            for last in ends:
                flat = []
                for i in range(prev, last + 1):
                    flat.extend((xs[i], ys[i], 1 if flags[i] & 0x01 else 0))
                contours.append(flat)
                prev = last + 1
            return contours
        # Composite glyph: stamp each component's own contours through
        # its offset (and optional uniform scale).
        pos = base + 10
        merged = []
        while True:
            cflags = u16(self.data, pos)
            comp_gid = u16(self.data, pos + 2)
            pos += 4
            if cflags & 0x0001:
                dx, dy = i16(self.data, pos), i16(self.data, pos + 2)
                pos += 4
            else:
                dx, dy = self.data[pos], self.data[pos + 1]
                pos += 2
            if not (cflags & 0x0002):
                dx, dy = 0, 0
            scale = 1.0
            if cflags & 0x0008:
                scale = i16(self.data, pos) / 16384.0
                pos += 2
            elif cflags & 0x0040:
                pos += 4
            elif cflags & 0x0080:
                pos += 8
            for contour in self.glyph_points(comp_gid, depth + 1):
                moved = []
                for i in range(0, len(contour), 3):
                    moved.extend((round(contour[i] * scale) + dx,
                                  round(contour[i + 1] * scale) + dy,
                                  contour[i + 2]))
                merged.append(moved)
            if not (cflags & 0x0020):
                break
        return merged

    def extract(self):
        glyphs = {}
        for ch in CHARSET:
            gid = self._cmap.get(ord(ch))
            if gid is None:
                continue
            glyphs[ch] = {
                "a": self.advance(gid),
                "c": self.glyph_points(gid),
            }
        return {"upem": self.upem, "glyphs": glyphs}


def main():
    out_path = os.path.join(HERE, "fonts.bin")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for key, filename in FACES.items():
            face = Face(os.path.join(HERE, "fonts", filename))
            payload = json.dumps(face.extract(), separators=(",", ":")).encode()
            info = zipfile.ZipInfo(key + ".json", date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, payload)
            print(key, "glyphs packed, json", len(payload), "bytes")
    print("fonts.bin:", os.path.getsize(out_path), "bytes")


if __name__ == "__main__":
    main()
