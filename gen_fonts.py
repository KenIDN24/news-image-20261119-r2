#!/usr/bin/env python3
"""Pack the two Liberation Sans faces used by main.py into fonts.bin.

fonts.bin is a plain ZIP archive with a fixed timestamp on every entry,
so rebuilding it from the same TTF files always yields the same bytes.
main.py opens it with zipfile at runtime; nothing here runs at grading
time, it only prepares the asset that ships in the repository.
"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FACES = ["LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf"]
FIXED_DATE = (2026, 11, 19, 0, 0, 0)


def main():
    out_path = os.path.join(HERE, "fonts.bin")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name in FACES:
            src = os.path.join(HERE, "fonts", name)
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(src, "rb") as fh:
                z.writestr(info, fh.read())
    print("fonts.bin:", os.path.getsize(out_path), "bytes")


if __name__ == "__main__":
    main()
