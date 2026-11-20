import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value.lower().startswith("paste_your"):
        return ""
    return value


class Settings:
    GEMINI_API_KEY = read_key("GEMINI_API_KEY")
    GROQ_API_KEY = read_key("GROQ_API_KEY")
    OPENROUTER_API_KEY = read_key("OPENROUTER_API_KEY")

    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()
    GROQ_MODEL = os.getenv("GROQ_MODEL", "").strip()
    GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "").strip()
    OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "").strip()
    OPENROUTER_VISION_MODEL = os.getenv("OPENROUTER_VISION_MODEL", "").strip()

    PROVIDER_ORDER = [
        name.strip().lower()
        for name in os.getenv("PROVIDER_ORDER", "gemini").split(",")
        if name.strip()
    ]

    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "5"))
    MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

    ALLOWED_EXTENSIONS = [
        ext.strip().lower()
        for ext in os.getenv("ALLOWED_EXTENSIONS", "pdf,png,jpg,jpeg").split(",")
        if ext.strip()
    ]

    RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
    READ_RATE_LIMIT_PER_MINUTE = int(os.getenv("READ_RATE_LIMIT_PER_MINUTE", "120"))
    GLOBAL_DAILY_LIMIT = int(os.getenv("GLOBAL_DAILY_LIMIT", "150"))
    MAX_CONCURRENT_UPLOADS = int(os.getenv("MAX_CONCURRENT_UPLOADS", "2"))
    TRUSTED_PROXY_HOPS = int(os.getenv("TRUSTED_PROXY_HOPS", "0"))

    MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "5"))
    MIN_TEXT_CHARS = int(os.getenv("MIN_TEXT_CHARS", "50"))
    MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "15000"))
    MAX_IMAGE_SIDE = int(os.getenv("MAX_IMAGE_SIDE", "1600"))
    MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "12000000"))

    AI_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "60"))
    AI_MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "4096"))

    DATABASE_PATH = os.getenv(
        "DATABASE_PATH", os.path.join(BASE_DIR, "data", "doclens.db")
    )
    DATA_RETENTION_DAYS = int(os.getenv("DATA_RETENTION_DAYS", "30"))


settings = Settings()