import base64

import httpx

from app.config import settings

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class ProviderError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class AllProvidersFailed(Exception):
    def __init__(self, message: str, attempts: list):
        super().__init__(message)
        self.message = message
        self.attempts = attempts


def get_providers() -> dict:
    return {
        "gemini": {
            "label": "Gemini",
            "style": "gemini",
            "url": GEMINI_URL,
            "api_key": settings.GEMINI_API_KEY,
            "text_model": settings.GEMINI_MODEL,
            "vision_model": settings.GEMINI_MODEL,
            "max_images": 5,
        },
        "groq": {
            "label": "Groq",
            "style": "openai",
            "url": GROQ_URL,
            "api_key": settings.GROQ_API_KEY,
            "text_model": settings.GROQ_MODEL,
            "vision_model": settings.GROQ_VISION_MODEL,
            "max_images": 3,
        },
        "openrouter": {
            "label": "OpenRouter",
            "style": "openai",
            "url": OPENROUTER_URL,
            "api_key": settings.OPENROUTER_API_KEY,
            "text_model": settings.OPENROUTER_MODEL,
            "vision_model": settings.OPENROUTER_VISION_MODEL,
            "max_images": 3,
        },
    }


def choose_model(provider: dict, has_images: bool) -> str:
    if has_images:
        return provider["vision_model"]
    return provider["text_model"] or provider["vision_model"]


def describe_providers() -> list:
    described = []
    for name, provider in get_providers().items():
        has_key = bool(provider["api_key"])
        described.append(
            {
                "name": name,
                "label": provider["label"],
                "has_key": has_key,
                "text_ready": has_key
                and bool(provider["text_model"] or provider["vision_model"]),
                "vision_ready": has_key and bool(provider["vision_model"]),
                "model": provider["text_model"] or provider["vision_model"],
            }
        )
    return described


def explain_http_error(label: str, status_code: int) -> str:
    if status_code in (401, 403):
        return f"{label} rejected the API key. Check the key in the .env file."
    if status_code == 404:
        return f"{label} could not find that model. Check the model name in the .env file."
    if status_code == 429:
        return f"{label} free limit reached. Try again in a little while."
    if status_code == 400:
        return f"{label} did not accept the request. The model may not support this input."
    return f"{label} had a problem (status {status_code})."


async def post_json(label: str, url: str, headers: dict, body: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=settings.AI_TIMEOUT_SECONDS) as client:
            response = await client.post(url, headers=headers, json=body)
    except httpx.TimeoutException:
        raise ProviderError(f"{label} took too long to answer.")
    except httpx.HTTPError:
        raise ProviderError(f"Could not reach {label}. Check the internet connection.")

    if response.status_code != 200:
        raise ProviderError(explain_http_error(label, response.status_code))

    try:
        return response.json()
    except ValueError:
        raise ProviderError(f"{label} sent an answer we could not understand.")


def encode_image(image: dict) -> str:
    return base64.b64encode(image["data"]).decode("ascii")


async def call_gemini(
    provider: dict, model: str, system_prompt: str, user_text: str, images: list
) -> str:
    parts = []
    for image in images:
        parts.append(
            {"inlineData": {"mimeType": image["mime_type"], "data": encode_image(image)}}
        )
    parts.append({"text": user_text})

    body = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": settings.AI_MAX_OUTPUT_TOKENS,
            "responseMimeType": "application/json",
        },
    }
    headers = {
        "x-goog-api-key": provider["api_key"],
        "Content-Type": "application/json",
    }

    data = await post_json(
        provider["label"], provider["url"].format(model=model), headers, body
    )

    try:
        answer_parts = data["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in answer_parts)
    except (KeyError, IndexError, TypeError):
        raise ProviderError("Gemini returned no answer. The content may have been blocked.")

    if not text.strip():
        raise ProviderError("Gemini returned an empty answer.")
    return text


async def call_openai_style(
    provider: dict, model: str, system_prompt: str, user_text: str, images: list
) -> str:
    if images:
        user_content = [{"type": "text", "text": user_text}]
        for image in images:
            data_link = f"data:{image['mime_type']};base64,{encode_image(image)}"
            user_content.append({"type": "image_url", "image_url": {"url": data_link}})
    else:
        user_content = user_text

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0,
        "max_tokens": settings.AI_MAX_OUTPUT_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {provider['api_key']}",
        "Content-Type": "application/json",
    }

    data = await post_json(provider["label"], provider["url"], headers, body)

    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise ProviderError(f"{provider['label']} returned no answer.")

    if not text or not text.strip():
        raise ProviderError(f"{provider['label']} returned an empty answer.")
    return text


async def generate(
    system_prompt: str, user_text: str, images: list, on_attempt=None
) -> dict:
    providers = get_providers()
    has_images = len(images) > 0
    attempts = []

    async def notify(attempt: dict):
        if on_attempt is not None:
            await on_attempt(attempt)

    async def record(attempt: dict):
        attempts.append(attempt)
        await notify(attempt)

    for name in settings.PROVIDER_ORDER:
        provider = providers.get(name)
        if provider is None:
            await record(
                {"provider": name, "label": name, "status": "skipped", "detail": "Unknown provider name"}
            )
            continue

        label = provider["label"]

        if not provider["api_key"]:
            await record(
                {"provider": name, "label": label, "status": "skipped", "detail": "No API key set"}
            )
            continue

        model = choose_model(provider, has_images)
        if not model:
            reason = "No vision model set, so it cannot read images" if has_images else "No model set"
            await record(
                {"provider": name, "label": label, "status": "skipped", "detail": reason}
            )
            continue

        usable_images = images[: provider["max_images"]]

        await notify(
            {"provider": name, "label": label, "status": "trying", "detail": f"Asking {label} using {model}"}
        )

        try:
            if provider["style"] == "gemini":
                text = await call_gemini(provider, model, system_prompt, user_text, usable_images)
            else:
                text = await call_openai_style(provider, model, system_prompt, user_text, usable_images)
        except ProviderError as error:
            await record(
                {"provider": name, "label": label, "status": "failed", "detail": error.message}
            )
            continue

        detail = f"Answered using {model}"
        if len(usable_images) < len(images):
            detail += f" (read the first {len(usable_images)} of {len(images)} pages)"
        await record(
            {"provider": name, "label": label, "status": "success", "detail": detail}
        )
        return {"provider": name, "model": model, "text": text, "attempts": attempts}

    raise AllProvidersFailed("No AI provider could answer right now.", attempts)