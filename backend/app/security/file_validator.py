from app.config import settings
from app.security.sanitizer import sanitize_filename

FILE_SIGNATURES = {
    "pdf": b"%PDF-",
    "png": b"\x89PNG\r\n\x1a\n",
    "jpeg": b"\xff\xd8\xff",
}

EXTENSION_TO_TYPE = {
    "pdf": "pdf",
    "png": "png",
    "jpg": "jpeg",
    "jpeg": "jpeg",
}


class FileRejected(Exception):
    def __init__(self, message: str, status_code: int, checks: list):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.checks = checks


def detect_file_type(content: bytes):
    for file_type, signature in FILE_SIGNATURES.items():
        if content.startswith(signature):
            return file_type
    return None


def get_extension(filename: str) -> str:
    if "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def validate_upload(filename: str, content: bytes) -> dict:
    checks = []

    def passed(name: str, detail: str):
        checks.append({"name": name, "passed": True, "detail": detail})

    def reject(name: str, detail: str, status_code: int):
        checks.append({"name": name, "passed": False, "detail": detail})
        raise FileRejected(detail, status_code, checks)

    safe_name = sanitize_filename(filename)
    passed("Filename cleaned", f"Safe name: {safe_name}")

    extension = get_extension(safe_name)
    if extension not in settings.ALLOWED_EXTENSIONS or extension not in EXTENSION_TO_TYPE:
        allowed = ", ".join(settings.ALLOWED_EXTENSIONS)
        reject("File type allowed", f"Only these file types are allowed: {allowed}.", 415)
    passed("File type allowed", f".{extension} is allowed")

    size = len(content)
    if size == 0:
        reject("File is not empty", "The file is empty.", 400)
    passed("File is not empty", f"{size} bytes")

    if size > settings.MAX_FILE_SIZE_BYTES:
        reject(
            "File size within limit",
            f"The file is larger than {settings.MAX_FILE_SIZE_MB} MB.",
            413,
        )
    passed("File size within limit", f"The limit is {settings.MAX_FILE_SIZE_MB} MB")

    detected_type = detect_file_type(content)
    if detected_type is None:
        reject(
            "Real content check",
            "The file content is not a real PDF, PNG, or JPEG.",
            415,
        )
    passed("Real content check", f"Content looks like a real {detected_type.upper()}")

    if EXTENSION_TO_TYPE[extension] != detected_type:
        reject(
            "Extension matches content",
            f"The name says .{extension} but the content is {detected_type.upper()}.",
            400,
        )
    passed("Extension matches content", "Name and content agree")

    return {
        "safe_filename": safe_name,
        "extension": extension,
        "detected_type": detected_type,
        "size_bytes": size,
        "checks": checks,
    }