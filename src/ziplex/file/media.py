"""Free (no LLM call), metadata-only handling for binary media assets --
images, video, audio, fonts -- that collect_files()'s content-based binary
filter would otherwise drop entirely (see that module's own docstring).

Before this existed, a media file was invisible everywhere downstream: not
in `files`, not in `relationships`, not even resolvable as the *target* of
another file's reference (a Godot .tscn's `[ext_resource path="res://
player.png"]`, an `<img src="logo.png">` in an HTML file, a README naming a
screenshot) -- text_references.py already matches a text file's content
against the real collected-file list, so the fix that actually closes that
gap is letting these files *into* that list at all, not teaching every
downstream consumer about a new file type.

Deliberately metadata-only, never content-based: describing what an image
*shows* needs a vision-capable LLM call per file, a real cost that scales
with asset count -- explicitly out of scope here (see the project's own
"is reading images too costly" discussion this module exists to answer).
What's free is exactly what a file's own bytes already expose without
decoding it: its size, and -- for the four image formats common enough to
justify a hand-rolled header parser -- its pixel dimensions. No image
decode, no third-party imaging dependency.
"""

import struct
from pathlib import Path

from .textutil import read_text, human_size as _human_size

# extension (lowercase, with leading dot) -> broad media kind. Deliberately
# not exhaustive -- SVG stays out on purpose: it's valid UTF-8 text, already
# collected/compressed as a text file today (extract/text/), so folding it
# in here would regress it from real content to a metadata-only summary for
# no reason.
MEDIA_EXTENSIONS: dict[str, str] = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".bmp": "image", ".webp": "image", ".ico": "image", ".avif": "image",
    ".tiff": "image", ".tif": "image", ".heic": "image",

    ".mp4": "video", ".mov": "video", ".avi": "video", ".mkv": "video",
    ".webm": "video", ".flv": "video", ".wmv": "video",

    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".flac": "audio",
    ".m4a": "audio", ".aac": "audio", ".wma": "audio",

    ".ttf": "font", ".otf": "font", ".woff": "font", ".woff2": "font",
}


def is_media_file(file_path: str) -> str | None:
    """The media kind ("image"/"video"/"audio"/"font") for file_path's
    extension alone -- no content check. Correct on its own only for
    collect_files()'s "keep if text OR media" filter, where read_text()
    already independently checks content as the other half of that same
    condition. Anywhere a caller is about to actually *treat* a file as
    media (skip real security scanning, skip a real text summary) needs
    classify_media_file() below instead, not this.
    """
    return MEDIA_EXTENSIONS.get(Path(file_path).suffix.lower())


def classify_media_file(file_path: str) -> str | None:
    """The strict version of is_media_file(): the media kind only if the
    extension matches *and* the file is actually undecodable as text (see
    file/textutil.py's read_text()) -- extension alone is not enough to
    call something media. Without the content check, a file that merely
    carries a media extension but is genuinely text (a Git LFS pointer
    checked in for a real video/image, or a secrets file simply renamed to
    .mp3/.png) would get silently misclassified by name alone -- caught by
    review as a real security gap in scanner.py (a mislabeled secrets file
    would have skipped scanning entirely) and, for the same root reason, a
    correctness gap in packager.py (a Git LFS pointer's real, if boring,
    text content would never get read or summarized, replaced by a bogus
    metadata-only description of the pointer file's own tiny size).
    Centralized here so scanner.py and packager.py build on the same
    corrected check instead of each re-deriving it and risking drift.
    """
    kind = is_media_file(file_path)
    if kind is None:
        return None
    return kind if read_text(file_path) is None else None


def _image_dimensions(file_path: str) -> tuple[int, int] | None:
    """(width, height) parsed from just a format's own header bytes -- PNG/
    GIF/BMP/JPEG only, the formats common enough (web assets, game sprites,
    screenshots) to justify hand-rolling a parser for. Anything else (webp,
    ico, avif, tiff, heic) -- or a header that doesn't parse as expected --
    returns None; the caller still has a file size to report either way.
    """
    try:
        with open(file_path, "rb") as f:
            head = f.read(32)

            if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
                width, height = struct.unpack(">II", head[16:24])
                return width, height

            if head[:6] in (b"GIF87a", b"GIF89a") and len(head) >= 10:
                width, height = struct.unpack("<HH", head[6:10])
                return width, height

            if head[:2] == b"BM" and len(head) >= 26:
                f.seek(18)
                width, height = struct.unpack("<ii", f.read(8))
                return width, abs(height)  # a top-down BMP stores a negative height

            if head[:2] == b"\xff\xd8":
                # f's position is already past `head` (32 bytes read above),
                # not right after the 2-byte SOI marker _jpeg_dimensions()
                # expects to start walking from -- seek back first.
                f.seek(2)
                return _jpeg_dimensions(f)
    except (OSError, struct.error):
        return None
    return None


def _jpeg_dimensions(f) -> tuple[int, int] | None:
    """Walks JPEG markers looking for the first SOFn (start-of-frame)
    segment, which is where width/height actually live -- unlike PNG/GIF/
    BMP, a JPEG's dimensions aren't at a fixed offset, since an arbitrary
    number of metadata segments (EXIF, ICC profiles, ...) can precede it.
    `f` is already positioned just past the 0xFFD8 SOI marker.
    """
    while True:
        marker = f.read(2)
        if len(marker) < 2 or marker[0] != 0xFF:
            return None
        kind = marker[1]
        if kind in (0x01,) or 0xD0 <= kind <= 0xD7:
            continue  # markers with no length-prefixed payload (TEM, RST0-RST7)
        if kind in (0xD8, 0xD9):
            # An SOI reappearing mid-stream (concatenated/malformed data) or
            # EOI reached -- neither is a length-prefixed segment either, and
            # unlike RSTn this means the walk can't reliably continue: bail
            # rather than misreading whatever bytes come next as a length.
            return None

        length_bytes = f.read(2)
        if len(length_bytes) < 2:
            return None
        length = struct.unpack(">H", length_bytes)[0]
        if length < 2:
            return None

        if 0xC0 <= kind <= 0xCF and kind not in (0xC4, 0xC8, 0xCC):
            payload = f.read(5)
            if len(payload) < 5:
                return None
            height, width = struct.unpack(">HH", payload[1:5])
            return width, height

        f.seek(length - 2, 1)


def describe_media_file(file_path: str, kind: str) -> dict:
    """{"kind", "size_bytes", "width", "height"} -- the last two only ever
    populated for kind == "image" (and only when _image_dimensions() could
    parse that specific format's header); None on either otherwise. Same
    "always present, N/A is a real value not a missing key" convention
    tech_stack.py/project.security_scan already use elsewhere in this
    pipeline.
    """
    try:
        size_bytes = Path(file_path).stat().st_size
    except OSError:
        size_bytes = 0

    width = height = None
    if kind == "image":
        dims = _image_dimensions(file_path)
        if dims is not None:
            width, height = dims

    return {"kind": kind, "size_bytes": size_bytes, "width": width, "height": height}


def media_summary(file_path: str, kind: str) -> str:
    """The deterministic, no-LLM `summary` a media file gets in aif.json --
    same spirit as summarizer.py's _structural_summary() for signature-less
    text files: not a description of what the asset *shows* (see this
    module's own docstring for why that's out of scope), just what its own
    bytes already say about it. Always non-empty, so this never looks like
    generate_summaries()'s failure placeholder or trips confidence.py's
    check for it.
    """
    desc = describe_media_file(file_path, kind)
    size = _human_size(desc["size_bytes"])
    # `is not None`, not a bare truthiness check -- a legitimately-parsed
    # dimension of exactly 0 (a truncated/malformed but still header-
    # parseable image) is a real value, not "couldn't parse."
    if desc["width"] is not None and desc["height"] is not None:
        return f"[{kind} asset, {desc['width']}x{desc['height']}, {size}]"
    return f"[{kind} asset, {size}]"
