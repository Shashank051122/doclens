from app.services.ai_providers import generate
from app.services.invoice_schema import OutputInvalid, parse_ai_answer

MAX_ANSWER_ATTEMPTS = 2


class ExtractionFailed(Exception):
    def __init__(self, message: str, attempts: list, notes: list):
        super().__init__(message)
        self.message = message
        self.attempts = attempts
        self.notes = notes


async def ask_ai(prompt: dict, on_attempt=None) -> dict:
    user_text = prompt["user_text"]
    ai_attempts = []
    notes = []

    async def relay(attempt: dict):
        if attempt["status"] != "trying":
            ai_attempts.append(attempt)
        if on_attempt is not None:
            await on_attempt(attempt)

    for answer_number in range(1, MAX_ANSWER_ATTEMPTS + 1):
        result = await generate(
            prompt["system_prompt"], user_text, prompt["images"], on_attempt=relay
        )

        try:
            invoice = parse_ai_answer(result["text"])
        except OutputInvalid as error:
            notes.append(
                f"Answer {answer_number} from {result['provider']} could not be used: {error.message}"
            )
            user_text = (
                prompt["user_text"]
                + "\n\nIMPORTANT: Your previous answer could not be used because: "
                + error.message
                + " Reply again with the JSON object only."
            )
            continue

        notes.append(
            f"Answer {answer_number} from {result['provider']} was valid JSON in the expected format"
        )

        return {
            "data": invoice.model_dump(),
            "provider": result["provider"],
            "model": result["model"],
            "attempts": ai_attempts,
            "answers_used": answer_number,
            "notes": notes,
        }

    raise ExtractionFailed(
        "The AI answered, but the answer could not be turned into valid data.",
        ai_attempts,
        notes,
    )