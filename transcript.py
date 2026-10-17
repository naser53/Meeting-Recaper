"""Parse a Microsoft Teams transcript pasted as text, merge speaker turns,
detect microphone hand-overs in hall meetings, and format the result."""

import re
import textwrap
from collections import Counter

# ---------------------------------------------------------------- parsing

_TIME_WORDS = r"(?:\d+\s*hours?\s*)?(?:\d+\s*minutes?\s*)?(?:\d+\s*seconds?)?"
_TIME_CLOCK = r"\d{1,2}(?::\d{2}){1,2}"
HEADER_RE = re.compile(
    r"^(?P<name>\S.{0,80}?)\s+(?P<time>" + _TIME_WORDS + "|" + _TIME_CLOCK + r")\s*$",
    re.I,
)
# Lines Teams adds when the speaker ID changes, e.g. "47 minutes 13 seconds47:13"
JUNK_TIME_RE = re.compile(r"^(?:\d+\s*(?:hours?|minutes?|seconds?)\s*)+\d{1,2}(?::\d{2}){1,2}\s*$", re.I)
INITIALS_RE = re.compile(r"^[A-Z]{1,3}$")
FILLER_RE = re.compile(r"^(?:u+m+|u+h+|e+r+m*|hmm+|mm+)[.,!…]*$", re.I)


def decode_bytes(data):
    """Decode an uploaded file, trying the encodings Windows editors use."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_seconds(time_text):
    """'1 hour 1 minute 4 seconds' or '1:01:04' -> 3664."""
    t = time_text.strip().lower()
    if ":" in t:
        total = 0
        for part in t.split(":"):
            total = total * 60 + int(part)
        return total
    total = 0
    for value, unit in re.findall(r"(\d+)\s*(hour|minute|second)", t):
        total += int(value) * {"hour": 3600, "minute": 60, "second": 1}[unit]
    return total


def fmt_time(seconds):
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _header_match(line):
    m = HEADER_RE.match(line.strip())
    if not m or not re.search(r"\d", m.group("time")):
        return None
    name = m.group("name").strip()
    if len(name.split()) > 6 or name[-1] in ".,;:!?":
        return None
    return name, parse_seconds(m.group("time"))


def parse_blocks(text):
    """Return a list of blocks: {speaker, start, paragraphs}.

    Each paragraph is a content line; a block may hold several when Teams
    put a paragraph under the previous one without a header of its own.
    """
    lines = [ln.rstrip() for ln in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]

    # First pass: a header candidate only counts if its name is used at least
    # twice, so a sentence that happens to end in "5 minutes" is not a header.
    names = Counter()
    for ln in lines:
        h = _header_match(ln)
        if h:
            names[h[0]] += 1
    known = {n for n, c in names.items() if c >= 2} or set(names)

    blocks = []
    for i, raw in enumerate(lines):
        ln = raw.strip()
        if not ln:
            continue
        h = _header_match(ln)
        if h and h[0] in known:
            blocks.append({"speaker": h[0], "start": h[1], "paragraphs": []})
            continue
        if JUNK_TIME_RE.match(ln) or ln in known:
            continue
        if INITIALS_RE.match(ln):
            nxt = next((l.strip() for l in lines[i + 1:] if l.strip()), "")
            if nxt in known or _header_match(nxt):
                continue
        if not blocks:
            blocks.append({"speaker": "Unknown", "start": 0, "paragraphs": []})
        blocks[-1]["paragraphs"].append(ln)
    return [b for b in blocks if b["paragraphs"]]


# -------------------------------------------------------------- sentences

SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])[\"”’)\]]*\s+(?=[\"“‘(\[]?[A-Z0-9])")


def split_sentences(text):
    return [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]


def build_sentences(blocks):
    """Join consecutive blocks of the same speaker ID into runs, then split
    each run into sentences that remember their run, time and layout clues."""
    runs = []
    for b in blocks:
        if runs and runs[-1]["speaker"] == b["speaker"]:
            runs[-1]["blocks"].append(b)
        else:
            runs.append({"speaker": b["speaker"], "blocks": [b]})

    sentences = []
    for r_idx, run in enumerate(runs):
        text = ""
        marks = []  # (char offset, time, is_extra_paragraph)
        for b in run["blocks"]:
            for p_idx, para in enumerate(b["paragraphs"]):
                if text:
                    text += " "
                marks.append((len(text), b["start"], p_idx > 0))
                text += para
        pos = 0
        for s in split_sentences(text):
            start = text.find(s, pos)
            end = start + len(s)
            pos = end
            time = marks[0][1]
            new_para = False
            for off, t, extra in marks:
                if off <= start:
                    time = t
                if extra and start - 1 <= off < end and off > 0:
                    # a header-less paragraph starts at (or inside) this sentence
                    new_para = new_para or off <= start + 1
            sentences.append({
                "i": len(sentences),
                "run": r_idx,
                "speaker_id": run["speaker"],
                "time": time,
                "text": s,
                "new_para": new_para,
            })
    return runs, sentences


# ------------------------------------------------------ hand-over signals

NAME = r"([A-Z][\w'’\-]+(?:\s+[A-Z][\w'’\-]+)?)"
NOT_NAMES = {
    "I", "You", "We", "They", "He", "She", "It", "The", "This", "That", "So",
    "And", "But", "Everyone", "Everybody", "All", "Team", "Teams", "Guys",
    "Thank", "Thanks", "Yeah", "Yes", "Okay", "OK", "Well", "Now", "Then",
    "Mate", "Folks", "Again", "Very", "Great", "Hi", "Hello", "Over", "Back",
    "Our", "My", "Your", "Next", "First", "Some", "Them", "Him", "Her", "Us",
}

HANDOVER_NAMED = [
    re.compile(r"(?i:\b(?:hand(?:ing|ed)?(?:\s+it)?\s+over|pass(?:ing|ed)?(?:\s+it)?(?:\s+(?:on|over|back))?"
               r"|turn(?:ing)?\s+it\s+over|over(?:\s+back)?)\s+to\s+)" + NAME),
    re.compile(r"(?i:\b(?:we\s+have|we've\s+got|please\s+welcome|welcome\s+up|invite|call(?:ing)?\s+(?:up|on)"
               r"|bring(?:ing)?\s+(?:up|in|on))\s+)" + NAME + r"(?i:\s+(?:here|up|who|to|over|on\s+stage))"),
    re.compile(r"\b" + NAME + r"(?i:,?\s+(?:over\s+to\s+you|it'?s\s+(?:over\s+to\s+)?you|you'?re\s+up|the\s+floor\s+is\s+yours))"),
]
HANDOVER_UNNAMED = re.compile(
    r"(?i)\b(?:over(?:\s+back)?\s+to\s+you|that'?s\s+you\s+then|the\s+floor\s+is\s+yours|you'?re\s+up"
    r"|i'?ll\s+hand\s+(?:it\s+)?over|hand\s+(?:it\s+)?back|pass\s+(?:it\s+)?(?:on|back)\b(?!\s+to))"
)
THANKS_START = re.compile(
    r"^(?i:(?:hi|hello|hey|great|okay|ok|yeah|right|so)[,!.]?\s+(?:everyone|all|everybody)?[,!.]?\s*(?:and\s+)?)?"
    r"(?i:thank\s+you(?:\s+(?:very|so)\s+much)?|thanks(?:\s+(?:very|so)\s+much)?),?\s+" + NAME
)
GREETING_START = re.compile(
    r"^(?i:hi|hello|hey|good\s+(?:morning|afternoon|evening)|(?:that'?s|it'?s)\s+a\s+(?:great|good|fair|really\s+good)\s+question)\b"
)
SELF_INTRO = re.compile(r"(?i:\b(?:i'?m|i\s+am|my\s+name\s+is|this\s+is))\s+" + NAME)
CLOSING = re.compile(
    r"^(?i:(?:so,?\s+)?(?:yeah,?\s+)?(?:okay,?\s+)?(?:thank\s+you|thanks)(?:\s+(?:very|so)\s+much)?"
    r"(?:,?\s+(?:all|everyone|everybody|guys))?)[.!]?$"
)

WEIGHTS = {"handover": 3, "thanks": 3, "greeting": 2, "handover_unnamed": 2, "closing": 1, "paragraph": 1}
LABELS = {
    "handover": "hands over to {name}",
    "handover_unnamed": "hands over",
    "thanks": "thanks {name} (previous speaker)",
    "greeting": "opens with a greeting or answers a question",
    "closing": "previous speaker closes",
    "paragraph": "new paragraph in the transcript",
    "id_change": "speaker ID changes in Teams",
}


def _name(m):
    if not m:
        return None
    name = m.group(1).strip()
    words = name.split()
    while words and words[-1] in NOT_NAMES:
        words.pop()
    if not words or words[0] in NOT_NAMES:
        return None
    return " ".join(words)


def _signals(sentences):
    """Yield (position, kind, name) where position is the sentence index at
    which a new speaker is likely to start."""
    for s in sentences:
        t, i = s["text"], s["i"]
        named = None
        for rx in HANDOVER_NAMED:
            named = _name(rx.search(t))
            if named:
                break
        if named:
            yield i + 1, "handover", named
        elif HANDOVER_UNNAMED.search(t):
            yield i + 1, "handover_unnamed", None
        thanks = _name(THANKS_START.match(t))
        if thanks:
            yield i, "thanks", thanks
        elif GREETING_START.match(t):
            yield i, "greeting", None
        if CLOSING.match(t):
            yield i + 1, "closing", None
        if s["new_para"]:
            yield i, "paragraph", None


def detect_markpoints(sentences, hall_ids):
    """Return markpoints: places where a new speaker starts.

    Every speaker-ID change is a fixed markpoint. Inside the hall IDs,
    hand-over signals that sit close together are merged into one optional
    markpoint with a confidence and a suggested speaker name.
    """
    n = len(sentences)
    run_starts = {s["i"] for s in sentences if s["i"] == 0 or sentences[s["i"] - 1]["run"] != s["run"]}

    raw = [(p, k, nm) for p, k, nm in _signals(sentences) if 0 <= p <= n]
    raw.sort(key=lambda x: x[0])

    # cluster signals that are at most two sentences apart
    clusters = []
    for p, k, nm in raw:
        if clusters and p - clusters[-1]["last"] <= 2:
            clusters[-1]["sig"].append((p, k, nm))
            clusters[-1]["last"] = p
        else:
            clusters.append({"sig": [(p, k, nm)], "last": p})

    points = {}
    for i in sorted(run_starts):
        points[i] = {"pos": i, "fixed": True, "signals": ["id_change"], "next_name": None, "prev_name": None, "score": 9}

    for c in clusters:
        kinds = {k for _, k, _ in c["sig"]}
        # where does the new voice begin? Prefer an opening (thanks/greeting),
        # then the sentence after a hand-over, then anything else.
        pos = None
        for pref in ("thanks", "greeting", "handover", "handover_unnamed", "closing", "paragraph"):
            cand = [p for p, k, _ in c["sig"] if k == pref]
            if cand:
                pos = cand[0]
                break
        near_start = next((r for r in run_starts if abs(r - pos) <= 2), None)
        if near_start is not None:
            pos = near_start
        if pos >= n:
            continue
        next_name = next((nm for _, k, nm in c["sig"] if k == "handover" and nm), None)
        prev_name = next((nm for _, k, nm in c["sig"] if k == "thanks" and nm), None)
        score = sum(WEIGHTS[k] for k in kinds)
        if pos in points:
            pt = points[pos]
            pt["next_name"] = pt["next_name"] or next_name
            pt["prev_name"] = pt["prev_name"] or prev_name
            if not pt["fixed"]:
                pt["signals"] = sorted(set(pt["signals"]) | kinds)
                pt["score"] = max(pt["score"], score)
            continue
        if sentences[pos]["speaker_id"] not in hall_ids:
            continue
        points[pos] = {"pos": pos, "fixed": False, "signals": sorted(kinds),
                       "next_name": next_name, "prev_name": prev_name, "score": score}

    ordered = [points[k] for k in sorted(points)]

    # name each segment
    hall_no = 0
    known_names = []
    for idx, pt in enumerate(ordered):
        s = sentences[pt["pos"]]
        if s["speaker_id"] not in hall_ids:
            name = s["speaker_id"]
        else:
            nxt = ordered[idx + 1] if idx + 1 < len(ordered) else None
            seg_end = nxt["pos"] if nxt else n
            intro = None
            for ss in sentences[pt["pos"]:min(seg_end, pt["pos"] + 3)]:
                intro = _name(SELF_INTRO.search(ss["text"]))
                if intro:
                    break
            name = pt["next_name"] or intro or (nxt and nxt["prev_name"] and sentences[nxt["pos"]]["run"] == s["run"] and nxt["prev_name"]) \
                or (nxt and nxt["prev_name"])
            if not name:
                hall_no += 1
                name = f"Hall speaker {hall_no}"
        if name not in known_names:
            known_names.append(name)
        pt["name"] = name
        pt["time"] = s["time"]
        pt["time_label"] = fmt_time(s["time"])
        pt["confidence"] = "fixed" if pt["fixed"] else ("high" if pt["score"] >= 3 else "medium" if pt["score"] == 2 else "low")
        pt["enabled"] = pt["fixed"] or pt["score"] >= 2
        pt["reason"] = "; ".join(
            LABELS[k].format(name=(pt["next_name"] if k == "handover" else pt["prev_name"]) or "?")
            for k in pt["signals"]
        )
        if pt["pos"] == 0:
            pt["reason"] = "start of the transcript"
        pt["before"] = sentences[pt["pos"] - 1]["text"] if pt["pos"] > 0 else ""
        pt["after"] = s["text"]
    return ordered, known_names


# ------------------------------------------------------------ building

def build_turns(sentences, markpoints, drop_fillers=False):
    """Cut the sentence list at the enabled markpoints and merge neighbouring
    segments that end up with the same speaker name."""
    cuts = sorted((m for m in markpoints if m.get("enabled")), key=lambda m: m["pos"])
    turns = []
    for idx, m in enumerate(cuts):
        end = cuts[idx + 1]["pos"] if idx + 1 < len(cuts) else len(sentences)
        texts = [s["text"] for s in sentences[m["pos"]:end]]
        if drop_fillers:
            texts = [t for t in texts if not FILLER_RE.match(t)]
        if not texts:
            continue
        name = (m.get("name") or "").strip() or "Unknown"
        if turns and turns[-1]["speaker"] == name:
            turns[-1]["text"] += " " + " ".join(texts)
        else:
            turns.append({"speaker": name, "time": sentences[m["pos"]]["time"], "text": " ".join(texts)})
    return turns


def _wrap_words(text, limit):
    return textwrap.wrap(text, width=limit, break_long_words=False, break_on_hyphens=False) or [text]


def _split_long_sentence(sentence, limit):
    """Break one over-long sentence at commas or semicolons where possible,
    then at word boundaries."""
    if len(sentence) <= limit:
        return [sentence]
    parts = re.split(r"(?<=[,;:])\s+", sentence)
    lines, cur = [], ""
    for p in parts:
        cand = f"{cur} {p}".strip()
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        if len(p) <= limit:
            cur = p
        else:
            wrapped = _wrap_words(p, limit)
            lines.extend(wrapped[:-1])
            cur = wrapped[-1]
    if cur:
        lines.append(cur)
    return lines


def format_lines(text, mode, limit=150):
    """auto:     pack whole sentences into lines of at most `limit` letters,
                 breaking at a full stop wherever possible
       sentence: one sentence per line
       fixed:    break every `limit` letters at a word boundary"""
    if mode == "fixed":
        return _wrap_words(text, limit)
    sentences = split_sentences(text)
    if mode == "sentence":
        return sentences
    lines, cur = [], ""
    for s in sentences:
        cand = f"{cur} {s}".strip()
        if len(cand) <= limit:
            cur = cand
            continue
        if cur:
            lines.append(cur)
        pieces = _split_long_sentence(s, limit)
        lines.extend(pieces[:-1])
        cur = pieces[-1]
    if cur:
        lines.append(cur)
    return lines


def render(turns, mode, show_time=False):
    out = []
    structured = []
    for t in turns:
        header = f"{t['speaker']} ({fmt_time(t['time'])})" if show_time else t["speaker"]
        lines = format_lines(t["text"], mode)
        structured.append({"header": header, "speaker": t["speaker"], "lines": lines})
        out.append(header + "\n" + "\n".join(lines))
    return "\n\n".join(out) + "\n", structured


# ------------------------------------------------------------ top level

def analyze(text, meeting_type, hall_ids=None):
    blocks = parse_blocks(text)
    runs, sentences = build_sentences(blocks)
    words = Counter()
    for s in sentences:
        words[s["speaker_id"]] += len(s["text"].split())
    ids = [{"id": k, "words": v} for k, v in words.most_common()]

    if meeting_type == "teams":
        hall = set()
    elif meeting_type == "hall":
        hall = set(words)
    else:
        hall = set(hall_ids) if hall_ids else ({ids[0]["id"]} if ids else set())

    markpoints, names = detect_markpoints(sentences, hall)
    return {
        "sentences": sentences,
        "markpoints": markpoints,
        "names": names,
        "ids": ids,
        "hall_ids": sorted(hall),
        "stats": {"blocks": len(blocks), "runs": len(runs), "sentences": len(sentences)},
    }
