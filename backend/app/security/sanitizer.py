import os
import re
import unicodedata

MAX_FILENAME_LENGTH = 100


def sanitize_text(text: str, max_length: int = 2000) -> str:
    if not text:
        return ""

    text = unicodedata.normalize("NFKC", text)

    cleaned_characters = []
    for character in text:
        if character in ("\n", "\t"):
            cleaned_characters.append(character)
        elif unicodedata.category(character).startswith("C"):
            continue
        else:
            cleaned_characters.append(character)

    return "".join(cleaned_characters).strip()[:max_length]


def sanitize_filename(filename: str) -> str:
    name = (filename or "").replace("\\", "/")
    name = os.path.basename(name)
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    name = name.lstrip(".")

    if not name:
        name = "document"

    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, extension = name.rpartition(".")
        if dot and len(extension) <= 10:
            name = stem[: MAX_FILENAME_LENGTH - len(extension) - 1] + "." + extension
        else:
            name = name[:MAX_FILENAME_LENGTH]

    return name