"""Stage 2 of the pipeline: constrain an LLM to the fixed taxonomy.

Given a URL plus the text fetched from the page, ask an OpenAI-compatible
chat model to return a single JSON object describing the bookmark. The model
output is validated/coerced on the way back in, and every failure path
degrades gracefully to a safe "Other / don't keep" result.
"""
import copy
import json
import re

import requests

import config

def build_system_prompt(taxonomy):
    """Build the system prompt for a given list of (name, description) tags.

    ``taxonomy`` is a list of ``Tag``-like objects (anything with ``.name`` and
    ``.description``) or plain category-name strings.
    """
    lines = []
    for tag in taxonomy:
        name = getattr(tag, "name", tag)
        desc = getattr(tag, "description", "") or ""
        lines.append(f"    - {name}" + (f": {desc}" if desc else ""))
    category_list = "\n".join(lines)

    return (
        "You categorize saved web links. You will be given a URL and text extracted "
        "from the page. Respond with ONLY a single JSON object (no markdown, no prose) "
        "with exactly these keys:\n"
        '  "title": a concise human-readable title for the link,\n'
        '  "category": exactly one of the category names listed below,\n'
        '  "summary": one or two sentences describing the link,\n'
        '  "keep_as_bookmark": a boolean, true if the link is worth keeping,\n'
        '  "reason": a short explanation for the keep decision.\n'
        "Available categories:\n" + category_list + "\n"
        "Choose the single best-fitting category by its exact name. "
        "Do not invent new categories."
    )


def _category_names(taxonomy):
    """Normalize a taxonomy into the set of valid category-name strings."""
    return [getattr(tag, "name", tag) for tag in taxonomy]


def _parse_json(content, names):
    """Extract and validate the JSON object from a model response.

    Strips stray markdown fences, regex-extracts the first {...} block, and
    coerces an unrecognized category to a safe fallback. Returns None if no
    usable object can be recovered.
    """
    if not content:
        return None

    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
    cleaned = re.sub(r"```$", "", cleaned).strip()

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None

    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    result = copy.deepcopy(config.DEFAULT_RESULT)
    result["title"] = str(data.get("title", "") or "")
    result["summary"] = str(data.get("summary", "") or "")
    result["reason"] = str(data.get("reason", "") or "")
    result["keep_as_bookmark"] = bool(data.get("keep_as_bookmark", False))

    fallback = "Other" if "Other" in names else (names[0] if names else "Other")
    category = data.get("category")
    result["category"] = category if category in names else fallback

    return result


def _call_model(model, url, text, taxonomy):
    """Make one chat-completion request and return the parsed result.

    Raises on HTTP / network / parse failure so the caller can fall back.
    """
    payload = {
        "model": model,
        "temperature": config.TEMPERATURE,
        "messages": [
            {"role": "system", "content": build_system_prompt(taxonomy)},
            {
                "role": "user",
                "content": f"URL: {url}\n\nPage text:\n{text}",
            },
        ],
    }
    headers = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"

    resp = requests.post(
        config.AI_CHAT_URL,
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]

    parsed = _parse_json(content, _category_names(taxonomy))
    if parsed is None:
        raise ValueError("could not parse JSON from model response")
    return parsed


def classify(url, text, taxonomy=None):
    """Classify a link, trying the primary model then the free fallback.

    ``taxonomy`` is the list of tags (Tag objects or name strings) the model
    must choose from; it falls back to config.CATEGORIES when not supplied.

    Never raises: if both models fail (error, 429, or unparseable output),
    returns a copy of DEFAULT_RESULT.
    """
    if taxonomy is None:
        taxonomy = config.CATEGORIES
    for model in (config.PRIMARY_MODEL, config.FALLBACK_MODEL):
        try:
            return _call_model(model, url, text, taxonomy)
        except Exception:
            continue
    return copy.deepcopy(config.DEFAULT_RESULT)
