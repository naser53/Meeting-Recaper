"""Recaper: clean up Microsoft Teams transcripts and translate them.

Run:  python app.py   then open http://127.0.0.1:5000
"""

import re
import time
from pathlib import Path

import json
import sys
import urllib.error
import urllib.request

try:
    from flask import Flask, jsonify, render_template, request
except ModuleNotFoundError:
    sys.exit("Flask is not installed. Run:  python -m pip install -r requirements.txt  (or double-click run.bat)")

import transcript as T

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB uploads

# Each translate request carries only a few lines; the page sends them one
# batch after another and joins the results back together in order.
BATCH = {
    "deepl": {"lines": 20, "chars": 6000},
    "ollama": {"lines": 15, "chars": 3000},
    "google": {"lines": 12, "chars": 1500},
}
OLLAMA_CLOUD = "https://ollama.com"
OLLAMA_DEFAULT_MODEL = "gpt-oss:120b"
API_FILE = Path(__file__).with_name("API.txt")

LANGUAGES = [
    ("fa", "Persian (فارسی)"), ("en", "English"), ("ar", "Arabic"), ("tr", "Turkish"),
    ("fr", "French"), ("de", "German"), ("es", "Spanish"), ("it", "Italian"),
    ("ru", "Russian"), ("zh-CN", "Chinese (Simplified)"), ("hi", "Hindi"), ("ur", "Urdu"),
    ("iw", "Hebrew"), ("ja", "Japanese"), ("ko", "Korean"), ("pt", "Portuguese"), ("nl", "Dutch"),
]
RTL_LANGS = ["fa", "ar", "ur", "iw", "ps", "ku"]
# DeepL uses its own codes for a few languages
DEEPL_CODES = {"en": "EN-US", "pt": "PT-PT", "zh-CN": "ZH-HANS", "iw": "HE"}


@app.route("/")
def index():
    return render_template("index.html", languages=LANGUAGES, rtl_langs=RTL_LANGS, batch=BATCH,
                           has_deepl=bool(deepl_key()), has_ollama=ollama_ready(),
                           ollama_model=api_value("Ollama Model") or OLLAMA_DEFAULT_MODEL)


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file received."), 400
    return jsonify(name=f.filename, text=T.decode_bytes(f.read()))


@app.post("/api/analyze")
def analyze():
    data = request.get_json(force=True)
    text = data.get("text") or ""
    if not text.strip():
        return jsonify(error="The file is empty."), 400
    result = T.analyze(text, data.get("type", "teams"), data.get("hall_ids"))
    if not result["sentences"]:
        return jsonify(error="No transcript lines were found in this file."), 400
    return jsonify(result)


@app.post("/api/build")
def build():
    data = request.get_json(force=True)
    sentences = data["sentences"]
    turns = T.build_turns(sentences, data["markpoints"], drop_fillers=data.get("drop_fillers", False))
    text, structured = T.render(turns, data.get("format", "auto"), show_time=data.get("show_time", False))
    return jsonify(text=text, turns=structured)


# ------------------------------------------------------------ DeepL

def api_value(label):
    """Read a value such as "DeepL API Key: ..." from API.txt next to this file.
    It is read on every request, so a changed key works without a restart."""
    try:
        text = API_FILE.read_text(encoding="utf-8-sig")
    except OSError:
        return None
    m = re.search(r"^\s*" + r"\s+".join(map(re.escape, label.split())) + r"\s*:\s*(\S+)", text, re.I | re.M)
    return m.group(1).strip() if m else None


def deepl_key():
    return api_value("DeepL API Key")


def deepl_url(key, path):
    host = "api-free.deepl.com" if key.endswith(":fx") else "api.deepl.com"
    return f"https://{host}/v2/{path}"


DEEPL_ERRORS = {
    403: "DeepL refused the API key. Check the key in API.txt.",
    456: "The DeepL monthly character quota is used up.",
}


def deepl_call(key, path, payload=None):
    """Call the DeepL API with the standard library only. Returns (status, body)."""
    req = urllib.request.Request(
        deepl_url(key, path),
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers={"Authorization": f"DeepL-Auth-Key {key}", "Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")


def translate_deepl(lines, target):
    key = deepl_key()
    if not key:
        raise RuntimeError(f'No "DeepL API Key:" line found in {API_FILE.name}.')
    payload = {"text": lines, "target_lang": DEEPL_CODES.get(target, target).upper()}
    for attempt in range(4):
        status, body = deepl_call(key, "translate", payload)
        if status in (429, 500, 502, 503, 504) and attempt < 3:
            time.sleep(2 ** attempt)  # too many requests or a hiccup: wait and retry
            continue
        if status != 200:
            detail = DEEPL_ERRORS.get(status)
            if not detail:
                try:
                    detail = json.loads(body).get("message")
                except ValueError:
                    detail = body[:200]
            raise RuntimeError(f"DeepL error {status}: {detail}")
        return [t["text"] for t in json.loads(body)["translations"]]


@app.get("/api/deepl-usage")
def deepl_usage():
    key = deepl_key()
    if not key:
        return jsonify(available=False)
    try:
        status, body = deepl_call(key, "usage")
        if status != 200:
            return jsonify(available=True, error=f"DeepL error {status}")
        u = json.loads(body)
        return jsonify(available=True, used=u.get("character_count"), limit=u.get("character_limit"))
    except Exception as exc:
        return jsonify(available=True, error=str(exc))


# ------------------------------------------------------------ Ollama

def ollama_config():
    """Ollama Cloud with "Ollama API Key:", or a local server if API.txt has
    a line like "Ollama URL: http://localhost:11434"."""
    url = (api_value("Ollama URL") or OLLAMA_CLOUD).rstrip("/")
    return url, api_value("Ollama API Key")


def ollama_ready():
    url, key = ollama_config()
    return bool(key) or url != OLLAMA_CLOUD


def ollama_call(path, payload=None, timeout=300):
    url, key = ollama_config()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(
        url + path,
        data=json.dumps(payload).encode("utf-8") if payload is not None else None,
        headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body).get("error", body)
        except ValueError:
            pass
        if e.code in (401, 403):
            body = "Ollama refused the API key. Check the key in API.txt."
        raise RuntimeError(f"Ollama error {e.code}: {str(body)[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach Ollama at {url}: {e.reason}")


OLLAMA_SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {"type": "string"}}},
    "required": ["translations"],
}


def _ollama_batch(lines, language, model):
    numbered = "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines))
    system = (
        f"You are a professional translator. Translate meeting transcript lines from English into {language}. "
        "Keep the meaning, tone and names; write natural, fluent text. "
        f'Answer only with JSON: {{"translations": [...]}} holding exactly {len(lines)} strings, '
        "one translated line for each numbered input line, in the same order, without the numbers. "
        "Do not add notes, explanations or the original text."
    )
    for attempt in range(3):
        try:
            res = ollama_call("/api/chat", {
                "model": model,
                "stream": False,
                "format": OLLAMA_SCHEMA,
                "options": {"temperature": 0.2},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": numbered}],
            })
        except RuntimeError as exc:
            if attempt == 2 or "error 40" in str(exc) or "refused" in str(exc):
                raise
            time.sleep(2 ** attempt)
            continue
        content = (res.get("message") or {}).get("content", "")
        try:
            out = json.loads(content)["translations"]
        except (ValueError, KeyError, TypeError):
            out = None
        if isinstance(out, list) and len(out) == len(lines):
            return [re.sub(r"^\s*\d+[.)]\s*", "", str(t)).strip() for t in out]
    return None


def translate_ollama(lines, target, model):
    language = dict(LANGUAGES).get(target, target).split(" (")[0]
    model = (model or api_value("Ollama Model") or OLLAMA_DEFAULT_MODEL).strip()

    def run(part):
        out = _ollama_batch(part, language, model)
        if out is not None:
            return out
        if len(part) == 1:
            raise RuntimeError("Ollama did not return a usable translation. Try another model.")
        # the model returned the wrong number of lines: send each half on its own
        mid = len(part) // 2
        return run(part[:mid]) + run(part[mid:])

    return run(lines)


@app.get("/api/ollama-models")
def ollama_models():
    try:
        return jsonify(models=[m["name"] for m in ollama_call("/api/tags", timeout=20).get("models", [])])
    except Exception as exc:
        return jsonify(models=[], error=str(exc))


# ------------------------------------------------------------ Google (fallback)

def _translate_one(translator, text):
    for attempt in range(3):
        try:
            return translator.translate(text) or ""
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))


def translate_google(lines, target):
    try:
        from deep_translator import GoogleTranslator
    except ModuleNotFoundError:
        raise RuntimeError("Google needs the deep-translator package: python -m pip install deep-translator")

    translator = GoogleTranslator(source="auto", target=target)

    def run(part):
        if len(part) == 1:
            return [_translate_one(translator, part[0]) if part[0].strip() else ""]
        out = _translate_one(translator, "\n".join(part)).split("\n")
        if len(out) == len(part):
            return out
        # the service merged or split lines: translate each half on its own
        mid = len(part) // 2
        return run(part[:mid]) + run(part[mid:])

    return run(lines)


@app.post("/api/translate")
def translate():
    """Translate one batch of lines; the page sends the transcript batch by batch."""
    data = request.get_json(force=True)
    lines = data.get("lines") or []
    target = data.get("target", "fa")
    engine = data.get("engine", "deepl")
    limit = BATCH.get(engine, BATCH["google"])
    if len(lines) > limit["lines"] or (len(lines) > 1 and sum(len(l) + 1 for l in lines) > limit["chars"] + 500):
        return jsonify(error="Batch too large."), 400
    if not lines:
        return jsonify(lines=[])
    try:
        if engine == "deepl":
            out = translate_deepl(lines, target)
        elif engine == "ollama":
            out = translate_ollama(lines, target, data.get("model"))
        else:
            out = translate_google(lines, target)
    except Exception as exc:
        return jsonify(error=f"Translation failed: {exc}"), 502
    return jsonify(lines=out)


if __name__ == "__main__":
    import os
    import threading
    import webbrowser

    if not os.environ.get("WERKZEUG_RUN_MAIN"):  # only once, not again in the reloader
        threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:5000")).start()
    app.run(debug=True)
