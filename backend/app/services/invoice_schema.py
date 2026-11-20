import json
import math
import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.security.sanitizer import sanitize_text

MAX_LINE_ITEMS = 100
CONFIDENCE_KEYS = ("vendor", "invoice_number", "invoice_date", "total")


class OutputInvalid(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def to_number(value):
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        number = float(value)
        return round(number, 4) if math.isfinite(number) else None

    if not isinstance(value, str):
        return None

    cleaned = re.sub(r"[^\d.,\-]", "", value)
    if not re.search(r"\d", cleaned):
        return None

    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        if re.fullmatch(r"-?\d+,\d{1,2}", cleaned):
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")

    try:
        number = float(cleaned)
    except ValueError:
        return None

    return round(number, 4) if math.isfinite(number) else None


def to_text(value, max_length: int = 200):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        value = str(value)
    if not isinstance(value, str):
        return None

    cleaned = " ".join(sanitize_text(value, max_length).split())
    return cleaned or None


def to_date(value):
    text = to_text(value, 20)
    if text is None:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return text


def to_currency(value):
    text = to_text(value, 10)
    if text is None:
        return None
    text = text.upper()
    return text if re.fullmatch(r"[A-Z]{3}", text) else None


class LineItem(BaseModel):
    description: Optional[str] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    amount: Optional[float] = None

    @field_validator("description", mode="before")
    @classmethod
    def clean_description(cls, value):
        return to_text(value, 200)

    @field_validator("quantity", "unit_price", "amount", mode="before")
    @classmethod
    def clean_numbers(cls, value):
        return to_number(value)


class InvoiceData(BaseModel):
    vendor: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    currency: Optional[str] = None
    line_items: list[LineItem] = Field(default_factory=list)
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    total: Optional[float] = None
    confidence: dict[str, float] = Field(default_factory=dict)

    @field_validator("vendor", "invoice_number", mode="before")
    @classmethod
    def clean_text_fields(cls, value):
        return to_text(value, 200)

    @field_validator("invoice_date", mode="before")
    @classmethod
    def clean_date(cls, value):
        return to_date(value)

    @field_validator("currency", mode="before")
    @classmethod
    def clean_currency(cls, value):
        return to_currency(value)

    @field_validator("subtotal", "tax", "total", mode="before")
    @classmethod
    def clean_money(cls, value):
        return to_number(value)

    @field_validator("line_items", mode="before")
    @classmethod
    def clean_line_items(cls, value):
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)][:MAX_LINE_ITEMS]

    @field_validator("confidence", mode="before")
    @classmethod
    def clean_confidence(cls, value):
        if not isinstance(value, dict):
            return {}
        cleaned = {}
        for key in CONFIDENCE_KEYS:
            number = to_number(value.get(key))
            if number is not None:
                cleaned[key] = min(max(number, 0.0), 1.0)
        return cleaned


def parse_ai_answer(raw: str) -> InvoiceData:
    if not raw or len(raw) > 100000:
        raise OutputInvalid("The answer was empty or far too long.")

    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL | re.IGNORECASE)

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise OutputInvalid("The answer contained no JSON object.")

    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        raise OutputInvalid("The answer was not valid JSON.")

    if not isinstance(data, dict):
        raise OutputInvalid("The answer was not a JSON object.")

    try:
        return InvoiceData.model_validate(data)
    except ValidationError:
        raise OutputInvalid("The answer did not match the expected format.")