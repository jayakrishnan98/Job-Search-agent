import json
import logging
import re
import time

from config import (
    CLAUDE_API_KEY,
    CLAUDE_MODEL,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_SCORE_MODEL,
    resolve_ai_provider,
)

logger = logging.getLogger(__name__)

# Gemini 2.5 is closed to new API users; map to current Developer API models.
_RETIRED_GEMINI_MODELS = {
    "gemini-2.5-flash": "gemini-3.6-flash",
    "gemini-2.5-pro": "gemini-3.1-pro-preview",
    "gemini-2.5-flash-lite": "gemini-3.5-flash-lite",
}
_GEMINI_CAPACITY_FALLBACKS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.6-flash",
)


def _resolve_gemini_model(model: str) -> str:
    return _RETIRED_GEMINI_MODELS.get((model or "").strip(), model)


def _error_code(exc: Exception) -> int | None:
    for attr in ("code", "status_code"):
        value = getattr(exc, attr, None)
        if value is not None:
            return value
    return None


def _models_to_try(primary: str) -> list[str]:
    models = [primary]
    for fallback in _GEMINI_CAPACITY_FALLBACKS:
        if fallback not in models:
            models.append(fallback)
    return models


class AINotConfiguredError(RuntimeError):
    pass


def _gemini_text(response) -> str:
    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            part_text = (getattr(part, "text", None) or "").strip()
            if part_text:
                return part_text
    return ""


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model response")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise ValueError("No valid JSON found in model response")


def generate(
    system: str,
    user: str,
    *,
    max_tokens: int = 2000,
    json_mode: bool = False,
    model: str | None = None,
    purpose: str = "default",
) -> str:
    provider = resolve_ai_provider()
    if provider is None:
        raise AINotConfiguredError(
            "No AI provider configured. Set GEMINI_API_KEY or CLAUDE_API_KEY in .env"
        )

    if provider == "gemini":
        requested = model or (GEMINI_SCORE_MODEL if purpose == "score" else GEMINI_MODEL)
        chosen = _resolve_gemini_model(requested)
        return _call_gemini(system, user, max_tokens, json_mode, chosen)
    return _call_claude(system, user, max_tokens, model or CLAUDE_MODEL)


def _gemini_client():
    from google import genai

    # Do not copy project/location into os.environ. That makes the SDK mix
    # ADC/Bearer auth with the API key and yields 401 ACCESS_TOKEN_TYPE_UNSUPPORTED.
    return genai.Client(api_key=GEMINI_API_KEY.strip(), vertexai=False)


def _call_gemini(
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool,
    model: str,
) -> str:
    from google.genai import types
    from google.genai.errors import ClientError, ServerError

    config_kwargs: dict = {
        "system_instruction": system,
        "max_output_tokens": max_tokens,
        "temperature": 0.2,
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
    }
    if json_mode:
        config_kwargs["response_mime_type"] = "application/json"
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="MINIMAL")

    # Vertex/Agent Platform is disabled on this project (403 SERVICE_DISABLED)
    # and overwrote the real Developer API 503 in earlier runs.
    models = _models_to_try(model)
    last_error: Exception | None = None
    client = _gemini_client()

    for current_model in models:
        for attempt in range(1, 3):
            try:
                response = client.models.generate_content(
                    model=current_model,
                    contents=user,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                text = _gemini_text(response)
                if not text:
                    raise ValueError("Gemini returned an empty response")
                return text
            except (ClientError, ServerError) as exc:
                last_error = exc
                message = str(exc).lower()
                code = _error_code(exc)
                logger.warning(
                    "Gemini call failed (attempt %d/2, model=%s): %s",
                    attempt,
                    current_model,
                    exc,
                )
                if code in (401, 403):
                    break
                suggested = re.search(r"use models/([a-zA-Z0-9._-]+)", str(exc))
                is_missing_model = (
                    code == 404
                    or "NOT_FOUND" in str(exc)
                    or "no longer available" in message
                )
                if is_missing_model and suggested:
                    replacement = suggested.group(1)
                    if replacement not in models:
                        models.append(replacement)
                    break
                retryable = code in (429, 500, 503) or any(
                    token in message
                    for token in ("resource exhausted", "unavailable", "high demand", "timeout")
                )
                if retryable and attempt == 1:
                    time.sleep(2)
                    continue
                break
            except Exception as exc:
                last_error = exc
                message = str(exc).lower()
                retryable = any(
                    token in message
                    for token in ("429", "resource exhausted", "unavailable", "503", "500", "timeout")
                )
                logger.warning(
                    "Gemini call failed (attempt %d/2, model=%s): %s",
                    attempt,
                    current_model,
                    exc,
                )
                if retryable and attempt == 1:
                    time.sleep(2)
                    continue
                break

    if last_error and "401" in str(last_error):
        raise RuntimeError(
            "Gemini rejected the API key (401). Create a Gemini Developer API key "
            "at https://aistudio.google.com/apikey (starts with AIza), put it in "
            "GEMINI_API_KEY, and restart the API server. A Gemini Pro app "
            "subscription is not the same as an API key."
        ) from last_error
    if last_error and (
        "503" in str(last_error) or "high demand" in str(last_error).lower()
    ):
        raise RuntimeError(
            "Gemini is overloaded (503). Wait a minute and Score again."
        ) from last_error
    raise last_error or RuntimeError("Gemini call failed")


def _call_claude(system: str, user: str, max_tokens: int, model: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            message = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            return message.content[0].text
        except anthropic.APIError as exc:
            last_error = exc
            if attempt == 3:
                raise
            time.sleep(5 * attempt)
    raise last_error or RuntimeError("Claude call failed")
