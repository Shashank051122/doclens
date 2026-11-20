import re
import secrets

from app.security.sanitizer import sanitize_text

INJECTION_PATTERNS = [
    (
        "Ignore earlier instructions",
        r"\bignore\s+(all\s+|any\s+|the\s+)*(previous|prior|above|earlier|preceding)\s+(instructions?|prompts?|rules?|messages?|context)",
    ),
    (
        "Disregard instructions",
        r"\bdisregard\s+(all\s+|any\s+|the\s+|your\s+)*(previous|prior|above|earlier|instructions?|rules?|guidelines?)",
    ),
    (
        "Forget instructions",
        r"\bforget\s+(everything|all|your|the|any)\b.{0,30}\b(above|previous|prior|instructions?|rules?)",
    ),
    (
        "Role change",
        r"\b(you\s+are\s+now|from\s+now\s+on|pretend\s+(to\s+be|you\s+are))\b",
    ),
    (
        "Asks for the prompt",
        r"\b(reveal|show|print|repeat|display|leak)\b.{0,25}\b(system\s+prompt|your\s+(prompt|instructions?|rules?)|hidden\s+(prompt|instructions?))",
    ),
    (
        "Mentions system or developer message",
        r"\b(system|developer)\s+(prompt|message|instructions?)\b",
    ),
    (
        "Fake chat markers",
        r"(<\|[a-z_]+\|>|\[/?inst\]|<<\s*sys\s*>>)",
    ),
    (
        "New instructions",
        r"\bnew\s+instructions?\s*:",
    ),
    (
        "Override rules",
        r"\boverride\s+(the\s+|your\s+|all\s+)*(rules?|instructions?|safety|settings?|guidelines?)",
    ),
    (
        "Send data elsewhere",
        r"\b(send|post|forward|email|upload)\b.{0,50}\bto\s+https?://",
    ),
]

COMPILED_PATTERNS = [
    (name, re.compile(pattern, re.IGNORECASE)) for name, pattern in INJECTION_PATTERNS
]


def scan_for_injection(text: str) -> list:
    findings = []

    for name, pattern in COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            findings.append({"name": name, "snippet": sanitize_text(match.group(0), 80)})

    if "<<<" in text or ">>>" in text:
        findings.append(
            {
                "name": "Fake boundary marker",
                "snippet": "The text contains the characters <<< or >>>",
            }
        )

    return findings


def risk_level(findings: list) -> str:
    if len(findings) >= 2:
        return "high"
    if len(findings) == 1:
        return "low"
    return "none"


def make_boundary() -> str:
    return secrets.token_hex(8)


def wrap_document(text: str, boundary: str) -> str:
    safe_text = text.replace("<<<", "<<").replace(">>>", ">>")
    return f"<<<DOCUMENT_{boundary}>>>\n{safe_text}\n<<<END_DOCUMENT_{boundary}>>>"