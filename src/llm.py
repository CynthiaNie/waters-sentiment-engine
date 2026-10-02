"""One door to the language model, whichever provider is behind it.

LLM_PROVIDER=anthropic          Claude through the Anthropic SDK (default).
LLM_PROVIDER=openai_compatible  Any endpoint speaking the OpenAI chat-completions
                                format: Qwen (DashScope), DeepSeek, Zhipu GLM,
                                Doubao (Volcano Ark), or a company gateway.

Every caller asks for JSON matching a schema and gets a dict back. Providers
differ in how strictly they can enforce a schema, so callers must still check
what comes back; the classifier's verbatim-span check does exactly that.

Settings are read from the environment at call time, so a change saved in the
web app applies to the next call without a restart.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# Presets offered in the web app. Model names change often; the lists are
# suggestions and any name the provider accepts can be typed instead.
PROVIDERS: dict[str, dict[str, Any]] = {
    "claude": {"label": "Claude (Anthropic)", "kind": "anthropic", "base_url": "",
               "models": ["claude-sonnet-4-6", "claude-sonnet-5-5", "claude-opus-5-5",
                          "claude-haiku-4-5"]},
    "qwen": {"label": "Qwen 通义千问 (Alibaba DashScope)", "kind": "openai_compatible",
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
             "models": ["qwen-plus", "qwen-max", "qwen-turbo"]},
    "deepseek": {"label": "DeepSeek 深度求索", "kind": "openai_compatible",
                 "base_url": "https://api.deepseek.com", "models": ["deepseek-chat"]},
    "zhipu": {"label": "Zhipu GLM 智谱", "kind": "openai_compatible",
              "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "models": ["glm-4-plus", "glm-4-air"]},
    "doubao": {"label": "Doubao 豆包 (Volcano Engine Ark)", "kind": "openai_compatible",
               "base_url": "https://ark.cn-beijing.volces.com/api/v3", "models": []},
    "custom": {"label": "Other OpenAI-compatible endpoint", "kind": "openai_compatible",
               "base_url": "", "models": []},
}

# Claude models that accept server-side refusal fallbacks.
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1",
                    "claude-sonnet-5-5"}
# Endpoints found not to support json_schema output, remembered per process.
_NO_SCHEMA: set[str] = set()


class UserError(RuntimeError):
    """A failure worded for the person using the web app.

    The message is a fixed template plus values, so the web app can show it in
    the interface language: t(exc.template).format(**exc.kw).
    """

    def __init__(self, template: str, **kw: Any):
        self.template, self.kw = template, kw
        super().__init__(template.format(**kw))


class LLMError(UserError):
    pass


def preset() -> str:
    return os.getenv("LLM_PRESET", "claude")


def kind() -> str:
    return PROVIDERS.get(preset(), PROVIDERS["custom"])["kind"]


def api_key() -> str:
    if kind() == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY", "")
    return os.getenv("LLM_API_KEY", "")


def key_env() -> str:
    return "ANTHROPIC_API_KEY" if kind() == "anthropic" else "LLM_API_KEY"


def base_url() -> str:
    return (os.getenv("LLM_BASE_URL") or PROVIDERS.get(preset(), {}).get("base_url")
            or "").rstrip("/")


def classify_model() -> str:
    return os.getenv("CLASSIFY_MODEL") or _first_model()


def helper_model() -> str:
    if os.getenv("HELPER_MODEL"):
        return os.environ["HELPER_MODEL"]
    # Setup calls are few and judgement-heavy: on Claude use a stronger model.
    return "claude-opus-5-5" if kind() == "anthropic" else classify_model()


def _first_model() -> str:
    models = PROVIDERS.get(preset(), {}).get("models") or []
    return models[0] if models else ""


def describe() -> str:
    return f"{PROVIDERS.get(preset(), PROVIDERS['custom'])['label']}"


# --------------------------------------------------------------------- calls
def complete_json(system: str, user: str, schema: dict, *, model: str,
                  max_tokens: int = 8000) -> dict:
    if not api_key():
        raise LLMError("No API key for {provider}. Add it in Settings.", provider=describe())
    if not model:
        raise LLMError("No model chosen. Pick one in Settings.")
    if kind() == "anthropic":
        return _anthropic(system, user, schema, model, max_tokens)
    return _openai_compatible(system, user, schema, model, max_tokens)


def extract_json(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise LLMError("The model did not return JSON.")
        return json.loads(m.group(0))


def _anthropic(system: str, user: str, schema: dict, model: str, max_tokens: int) -> dict:
    import anthropic
    client = anthropic.Anthropic(api_key=api_key())
    output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
    if "haiku" not in model:
        output_config["effort"] = "medium"
    kwargs = dict(model=model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config=output_config)
    try:
        if model in _FALLBACK_MODELS:
            # On a policy decline the API re-runs the request on a fallback
            # model within the same call instead of returning nothing.
            msg = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            msg = client.messages.create(**kwargs)
    except anthropic.AuthenticationError:
        raise LLMError("The Anthropic API key was rejected. Check it in Settings.")
    except anthropic.NotFoundError:
        raise LLMError("Model {model} is not available to this key. Pick another in "
                       "Settings.", model=model)
    except anthropic.RateLimitError:
        raise LLMError("Rate limit reached. Wait a minute and retry.")
    except anthropic.APIConnectionError:
        raise LLMError("Could not reach the Anthropic API. Check the network.")
    if msg.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if msg.stop_reason == "max_tokens":
        raise LLMError("The model's answer was cut off.")
    return extract_json("".join(b.text for b in msg.content
                                if getattr(b, "type", "") == "text"))


def _openai_compatible(system: str, user: str, schema: dict, model: str,
                       max_tokens: int) -> dict:
    import httpx
    url = base_url()
    if not url:
        raise LLMError("No endpoint address set for this provider. Add it in Settings.")
    endpoint = f"{url}/chat/completions"
    # json_object mode requires the word JSON in the prompt and does not see the
    # schema, so the schema is always spelled out in the system prompt too.
    sys_full = (f"{system}\n\nReturn only a JSON object that matches this JSON "
                f"Schema:\n{json.dumps(schema, ensure_ascii=False)}")
    body = {"model": model, "max_tokens": max_tokens, "temperature": 0,
            "messages": [{"role": "system", "content": sys_full},
                         {"role": "user", "content": user}]}
    modes = (["json_object"] if endpoint in _NO_SCHEMA
             else ["json_schema", "json_object"])
    last = None
    for mode in modes:
        body["response_format"] = (
            {"type": "json_schema",
             "json_schema": {"name": "result", "schema": schema, "strict": True}}
            if mode == "json_schema" else {"type": "json_object"})
        try:
            r = httpx.post(endpoint, json=body, timeout=180,
                           headers={"Authorization": f"Bearer {api_key()}"})
        except httpx.HTTPError as exc:
            raise LLMError("Could not reach {url}: {error}", url=url, error=str(exc))
        if r.status_code == 400 and mode == "json_schema":
            # Many compatible endpoints accept only json_object; fall back once
            # and remember, rather than failing every batch.
            _NO_SCHEMA.add(endpoint)
            last = r
            continue
        if r.status_code in (401, 403):
            raise LLMError("The API key was rejected. Check it in Settings.")
        if r.status_code == 404:
            raise LLMError("Model {model} or the endpoint address was not found.", model=model)
        if r.status_code == 429:
            raise LLMError("Rate limit or balance reached. Wait, or check the account.")
        if r.status_code >= 400:
            raise LLMError("Provider error {status}: {detail}", status=r.status_code,
                           detail=r.text[:300])
        data = r.json()
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("Unexpected response from the provider: {detail}",
                           detail=str(data)[:300])
        if choice.get("finish_reason") == "length":
            raise LLMError("The model's answer was cut off.")
        return extract_json(content)
    raise LLMError("Provider error {status}: {detail}", status=last.status_code,
                   detail=last.text[:300])


def ping() -> str:
    """Tiny round trip for the Settings 'Test' button. Returns the model used."""
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    model = classify_model()
    complete_json("Reply with JSON.", 'Return {"ok": true}.', schema, model=model,
                  max_tokens=2000)
    return model
