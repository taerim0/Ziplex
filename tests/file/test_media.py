import struct

from ziplex.file.media import (
    is_media_file,
    classify_media_file,
    describe_media_file,
    media_summary,
)


def _write_png(path, width, height):
    header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height)
    path.write_bytes(header + b"\x00" * 8)  # padding past the bytes the parser reads


def _write_gif(path, width, height):
    path.write_bytes(b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 4)


def _write_bmp(path, width, height):
    path.write_bytes(b"BM" + b"\x00" * 16 + struct.pack("<ii", width, height))


def _write_jpeg(path, width, height):
    soi = b"\xff\xd8"
    com_payload = b"hello"
    com = b"\xff\xfe" + struct.pack(">H", 2 + len(com_payload)) + com_payload
    sof_payload = bytes([8]) + struct.pack(">HH", height, width)
    sof = b"\xff\xc0" + struct.pack(">H", 2 + len(sof_payload)) + sof_payload
    path.write_bytes(soi + com + sof)


def test_is_media_file_recognizes_known_extensions(tmp_path):
    assert is_media_file("logo.png") == "image"
    assert is_media_file("clip.MP4") == "video"
    assert is_media_file("theme.ogg") == "audio"
    assert is_media_file("font.woff2") == "font"


def test_is_media_file_returns_none_for_unrecognized_extension():
    assert is_media_file("notes.txt") is None
    assert is_media_file("sprite.bin") is None
    # SVG is deliberately excluded -- it's valid text, already handled by
    # the normal text-collection/compression path
    assert is_media_file("icon.svg") is None


def test_describe_media_file_reads_png_dimensions(tmp_path):
    path = tmp_path / "hero.png"
    _write_png(path, 1920, 1080)

    desc = describe_media_file(str(path), "image")
    assert desc["kind"] == "image"
    assert desc["width"] == 1920
    assert desc["height"] == 1080
    assert desc["size_bytes"] == path.stat().st_size


def test_describe_media_file_reads_gif_dimensions(tmp_path):
    path = tmp_path / "anim.gif"
    _write_gif(path, 64, 32)

    desc = describe_media_file(str(path), "image")
    assert (desc["width"], desc["height"]) == (64, 32)


def test_describe_media_file_reads_bmp_dimensions(tmp_path):
    path = tmp_path / "shot.bmp"
    _write_bmp(path, 200, 100)

    desc = describe_media_file(str(path), "image")
    assert (desc["width"], desc["height"]) == (200, 100)


def test_describe_media_file_reads_bmp_dimensions_with_negative_top_down_height(tmp_path):
    # a top-down BMP stores its height as a negative int -- must come back
    # as a positive pixel count either way
    path = tmp_path / "shot.bmp"
    _write_bmp(path, 200, -100)

    desc = describe_media_file(str(path), "image")
    assert (desc["width"], desc["height"]) == (200, 100)


def test_describe_media_file_reads_jpeg_dimensions_past_a_leading_segment(tmp_path):
    # the comment segment before SOF0 exercises the marker-walking loop --
    # dimensions aren't at a fixed offset in JPEG the way they are for the
    # other three formats
    path = tmp_path / "photo.jpg"
    _write_jpeg(path, 800, 600)

    desc = describe_media_file(str(path), "image")
    assert (desc["width"], desc["height"]) == (800, 600)


def test_describe_media_file_leaves_dimensions_none_for_unparsed_image_formats(tmp_path):
    path = tmp_path / "icon.webp"
    path.write_bytes(b"RIFF" + b"\x00" * 20)

    desc = describe_media_file(str(path), "image")
    assert desc["width"] is None
    assert desc["height"] is None
    assert desc["size_bytes"] == path.stat().st_size


def test_describe_media_file_never_looks_for_dimensions_on_non_image_kinds(tmp_path):
    path = tmp_path / "theme.mp3"
    path.write_bytes(b"\x00" * 10)

    desc = describe_media_file(str(path), "audio")
    assert desc["kind"] == "audio"
    assert desc["width"] is None
    assert desc["height"] is None


def test_media_summary_includes_dimensions_for_a_parsed_image(tmp_path):
    path = tmp_path / "hero.png"
    _write_png(path, 1920, 1080)

    summary = media_summary(str(path), "image")
    assert summary.startswith("[image asset, 1920x1080,")
    assert summary.endswith("]")


def test_media_summary_falls_back_to_size_only_when_dimensions_are_unknown(tmp_path):
    path = tmp_path / "theme.mp3"
    path.write_bytes(b"\x00" * 10)

    summary = media_summary(str(path), "audio")
    assert summary == "[audio asset, 10B]"


def test_media_summary_includes_a_legitimately_zero_dimension(tmp_path):
    # a width/height of exactly 0 (a truncated/malformed but still header-
    # parseable image) is a real parsed value, not "couldn't parse" -- must
    # not be silently dropped by a bare truthiness check
    path = tmp_path / "broken.png"
    _write_png(path, 0, 100)

    desc = describe_media_file(str(path), "image")
    assert (desc["width"], desc["height"]) == (0, 100)
    assert media_summary(str(path), "image") == "[image asset, 0x100, 32B]"


def test_classify_media_file_returns_none_for_a_media_extension_that_is_actually_text(tmp_path):
    # a Git LFS pointer file (or any mislabeled text file) checked in with a
    # media extension must not be classified as media -- extension alone
    # isn't enough, the content has to actually be undecodable as text too
    path = tmp_path / "video.mp4"
    path.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 123\n", encoding="utf-8")

    assert is_media_file(str(path)) == "video"  # extension still matches
    assert classify_media_file(str(path)) is None  # but it's real text


def test_classify_media_file_returns_the_kind_for_genuinely_binary_media(tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(bytes(range(256)))

    assert classify_media_file(str(path)) == "image"


def test_classify_media_file_returns_none_for_a_non_media_extension():
    assert classify_media_file("notes.txt") is None
