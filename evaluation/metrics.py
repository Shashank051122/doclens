import re
from difflib import SequenceMatcher

FIELDS = [
    "vendor",
    "invoice_number",
    "invoice_date",
    "currency",
    "subtotal",
    "tax",
    "total",
]
NUMBER_FIELDS = ("subtotal", "tax", "total")
MONEY_TOLERANCE = 0.01


def normalize_text(value) -> str:
    if value is None:
        return ""
    text = re.sub(r"[^\w\s]", " ", str(value).casefold())
    return " ".join(text.split())


def similarity(first, second) -> float:
    return SequenceMatcher(None, normalize_text(first), normalize_text(second)).ratio()


def compare_field(field: str, expected, predicted) -> str:
    if expected is None and predicted is None:
        return "correct"
    if expected is None:
        return "invented"
    if predicted is None:
        return "missing"

    if field in NUMBER_FIELDS:
        try:
            difference = abs(float(expected) - float(predicted))
        except (TypeError, ValueError):
            return "wrong"
        return "correct" if difference <= MONEY_TOLERANCE else "wrong"

    if field == "invoice_date":
        return "correct" if str(expected) == str(predicted) else "wrong"

    if field == "currency":
        return "correct" if str(expected).upper() == str(predicted).upper() else "wrong"

    if field == "invoice_number":
        first = normalize_text(expected).replace(" ", "")
        second = normalize_text(predicted).replace(" ", "")
        return "correct" if first == second else "wrong"

    return "correct" if similarity(expected, predicted) >= 0.9 else "wrong"


def amounts_close(first, second) -> bool:
    if first is None or second is None:
        return False
    try:
        return abs(float(first) - float(second)) <= MONEY_TOLERANCE
    except (TypeError, ValueError):
        return False


def match_line_items(expected_items: list, predicted_items: list) -> dict:
    unmatched = list(predicted_items)
    matched = 0

    for expected in expected_items:
        best_index = None
        best_score = 0.0

        for index, candidate in enumerate(unmatched):
            score = similarity(expected.get("description"), candidate.get("description"))
            if amounts_close(expected.get("amount"), candidate.get("amount")) and score >= 0.6:
                if score > best_score:
                    best_index = index
                    best_score = score

        if best_index is not None:
            matched += 1
            unmatched.pop(best_index)

    if predicted_items:
        precision = matched / len(predicted_items)
    else:
        precision = 1.0 if not expected_items else 0.0

    if expected_items:
        recall = matched / len(expected_items)
    else:
        recall = 1.0 if not predicted_items else 0.0

    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return {
        "expected": len(expected_items),
        "predicted": len(predicted_items),
        "matched": matched,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def score_document(expected: dict, predicted: dict) -> dict:
    fields = {
        field: compare_field(field, expected.get(field), predicted.get(field))
        for field in FIELDS
    }
    items = match_line_items(
        expected.get("line_items") or [], predicted.get("line_items") or []
    )

    fields_correct = sum(1 for status in fields.values() if status == "correct")
    all_correct = fields_correct == len(FIELDS) and items["f1"] >= 0.999

    return {
        "fields": fields,
        "line_items": items,
        "fields_correct": fields_correct,
        "all_correct": all_correct,
    }