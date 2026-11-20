import io

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.config import settings
from app.security.sanitizer import sanitize_text

Image.MAX_IMAGE_PIXELS = settings.MAX_IMAGE_PIXELS


class DocumentUnreadable(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def prepare_image(image: Image.Image) -> dict:
    image = ImageOps.exif_transpose(image)

    has_transparency = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_transparency:
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    image.thumbnail((settings.MAX_IMAGE_SIDE, settings.MAX_IMAGE_SIDE))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85)
    return {"mime_type": "image/jpeg", "data": buffer.getvalue()}


def read_image(content: bytes) -> dict:
    notes = []

    try:
        image = Image.open(io.BytesIO(content))
        width, height = image.size
        if width * height > settings.MAX_IMAGE_PIXELS:
            raise DocumentUnreadable(
                f"This image is too large ({width} x {height} pixels)."
            )
        image.load()
    except DocumentUnreadable:
        raise
    except Exception:
        raise DocumentUnreadable("Could not read this image. It may be damaged.")

    notes.append(f"Image is {width} x {height} pixels")

    prepared = prepare_image(image)
    size_kb = len(prepared["data"]) // 1024
    notes.append(f"Resized and cleaned, metadata removed ({size_kb} KB)")

    return {
        "mode": "images",
        "total_pages": 1,
        "pages_read": 1,
        "text": "",
        "images": [prepared],
        "notes": notes,
    }


def read_pdf(content: bytes) -> dict:
    notes = []

    try:
        pdf = pdfium.PdfDocument(content)
    except Exception:
        raise DocumentUnreadable(
            "Could not open this PDF. It may be damaged or password protected."
        )

    try:
        total_pages = len(pdf)
        if total_pages == 0:
            raise DocumentUnreadable("This PDF has no pages.")

        pages_to_read = min(total_pages, settings.MAX_PDF_PAGES)
        notes.append(f"PDF has {total_pages} page(s), reading {pages_to_read}")

        page_texts = []
        for index in range(pages_to_read):
            page = pdf[index]
            textpage = page.get_textpage()
            page_texts.append(textpage.get_text_range())
            textpage.close()
            page.close()

        text = sanitize_text("\n\n".join(page_texts), settings.MAX_TEXT_CHARS)

        if len(text) >= settings.MIN_TEXT_CHARS:
            notes.append(f"Found {len(text)} characters of text, so no images needed")
            return {
                "mode": "text",
                "total_pages": total_pages,
                "pages_read": pages_to_read,
                "text": text,
                "images": [],
                "notes": notes,
            }

        notes.append("Almost no text found, so this looks like a scan. Making images")

        images = []
        for index in range(pages_to_read):
            page = pdf[index]
            width, height = page.get_size()
            longest_side = max(width, height) or 1
            scale = min(2.0, settings.MAX_IMAGE_SIDE / longest_side)
            bitmap = page.render(scale=scale)
            images.append(prepare_image(bitmap.to_pil()))
            page.close()

        notes.append(f"Made {len(images)} image(s) from the PDF pages")

        return {
            "mode": "images",
            "total_pages": total_pages,
            "pages_read": pages_to_read,
            "text": "",
            "images": images,
            "notes": notes,
        }

    except DocumentUnreadable:
        raise
    except Exception:
        raise DocumentUnreadable("Something went wrong while reading this PDF.")
    finally:
        pdf.close()


def read_document(content: bytes, file_type: str) -> dict:
    if file_type == "pdf":
        return read_pdf(content)
    return read_image(content)