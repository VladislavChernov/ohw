"""Генерация минимального валидного PDF (текст + image XObject) для тестов.

Извлекаемый текст включается в поток Contents как Tj-строки; изображение —
1x1 DeviceGray XObject в ресурсах страницы.
"""

from __future__ import annotations

import io


def _pdf_obj(number: int, body: str) -> bytes:
    return f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")


def _text_string(text: str) -> str:
    """Кодирует строку для PDF: всегда UTF-16BE Hex (Identity-H, 2-байтные коды)."""
    data = text.encode("utf-16-be")
    return "<" + "".join(f"{b:02x}" for b in data) + ">"


def _to_unicode_cmap(used_chars: str) -> bytes:
    """ToUnicode CMap: identity bfsrange для использованных символов."""
    lines = ["/CIDInit /ProcSet findresource begin", "12 dict begin", "begincmap"]
    lines += [
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        "/CMapName /Adobe-Identity-UCS def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        "<0000> <FFFF>",
        "endcodespacerange",
    ]
    codepoints = sorted({ord(c) for c in used_chars})
    bfchar = []
    for cp in codepoints:
        bfchar.append(f"<{cp:04X}> <{cp:04X}>")
    lines.append(f"{len(bfchar)} beginbfchar")
    lines += bfchar
    lines.append("endbfchar")
    lines += ["endcmap", "CMapName currentdict /CMap defineresource pop", "end", "end"]
    body = "\n".join(lines) + "\n"
    return (
        f"<< /Length {len(body)} >>\nstream\n{body}".encode("latin-1") + b"endstream\n"
    )


def make_pdf(text: str, include_image: bool = True) -> bytes:
    """Минимальный валидный PDF: Type0/Identity-H (Unicode) + image XObject."""
    shown = _text_string(text)
    contents = f"BT /F1 12 Tf 72 720 Td {shown} Tj ET\n"
    if include_image:
        contents += "q 100 0 0 100 200 200 cm /Im0 Do Q\n"
    xobjects = "/XObject << /Im0 6 0 R >> " if include_image else ""
    resources = f"<< /Font << /F1 4 0 R >> {xobjects}>>"

    stream = f"<< /Length {len(contents)} >>\nstream\n{contents}endstream\n"
    image_stream = b"\xff"

    # объекты: 1 catalog, 2 pages, 3 page, 4 font(Type0), 5 descendant(CIDFont),
    # 6 image, 7 ToUnicode, 8 contents
    objects: list[bytes] = [
        _pdf_obj(1, "<< /Type /Catalog /Pages 2 0 R >>"),
        _pdf_obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        _pdf_obj(
            3,
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources {resources} /Contents 8 0 R >>",
        ),
        _pdf_obj(
            4,
            "<< /Type /Font /Subtype /Type0 /BaseFont /ArialUni "
            "/Encoding /Identity-H /DescendantFonts [5 0 R] /ToUnicode 7 0 R >>",
        ),
        _pdf_obj(
            5,
            "<< /Type /Font /Subtype /CIDFontType2 /BaseFont /ArialUni "
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
            "/CIDToGIDMap /Identity >>",
        ),
        _pdf_obj(
            6,
            "<< /Type /XObject /Subtype /Image /Width 1 /Height 1 "
            f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length {len(image_stream)} >>\n"
            "stream\n" + image_stream.decode("latin-1") + "\nendstream",
        ),
    ]
    objects.append(
        "7 0 obj\n".encode("latin-1") + _to_unicode_cmap(text) + b"\nendobj\n"
    )
    objects.append(_pdf_obj(8, stream))

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for obj in objects:
        offsets.append(buf.tell())
        buf.write(obj)
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    buf.write(b"0000000000 65535 f \n")
    for off in offsets:
        buf.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    buf.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n"
        ).encode("latin-1")
    )
    return buf.getvalue()