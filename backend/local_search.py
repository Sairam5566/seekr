"""
Local search provider: a tiny BM25 index over data/corpus.json.

No network, no API key. The last word of the query is also matched as a
prefix, so "fasta" already finds "fastapi" — that is what makes results
arrive while you are still typing.
"""
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import quote, urlparse

CORPUS_PATH = Path(__file__).parent / "data" / "corpus.json"
PAGE_SIZE = 10

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in",
    "is", "it", "of", "on", "or", "the", "to", "what", "when", "where", "which",
    "who", "why", "with", "vs", "do", "i", "my",
}


def tokenize(text):
    return [t for t in TOKEN_RE.findall((text or "").lower()) if t not in STOPWORDS]


def display_link(link):
    try:
        host = urlparse(link).hostname or ""
    except ValueError:
        return None
    return host[4:] if host.startswith("www.") else (host or None)


def thumb_url(title, kind="image"):
    """A locally generated SVG tile (see /api/thumb), so images work offline."""
    return f"/api/thumb?kind={kind}&t={quote(title[:80])}"


class Bm25Index:
    """Okapi BM25 over a list of docs. `fields` maps field name → weight."""

    def __init__(self, docs, fields, k1=1.4, b=0.72):
        self.docs = docs
        self.k1, self.b = k1, b
        self.postings = {}  # term -> {doc_index: weighted term frequency}
        self.lengths = []
        for i, doc in enumerate(docs):
            tf = {}
            for field, weight in fields.items():
                value = doc.get(field) or ""
                if isinstance(value, list):
                    value = " ".join(value)
                for term in tokenize(value):
                    tf[term] = tf.get(term, 0) + weight
            self.lengths.append(sum(tf.values()) or 1)
            for term, freq in tf.items():
                self.postings.setdefault(term, {})[i] = freq
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1
        self.vocab = sorted(self.postings)

    def expand(self, term, is_last):
        """Exact term, plus prefix completions for the word still being typed."""
        if not is_last or len(term) < 2:
            return [term] if term in self.postings else []
        hits = [v for v in self.vocab if v.startswith(term)]
        # Prefer the exact term and short completions; cap to stay fast.
        hits.sort(key=lambda v: (v != term, len(v)))
        return hits[:8]

    def search(self, query, partial_last=True):
        terms = tokenize(query)
        if not terms:
            return []
        n = len(self.docs)
        scores, matched = {}, {}
        for idx, term in enumerate(terms):
            is_last = partial_last and idx == len(terms) - 1
            for variant in self.expand(term, is_last):
                posting = self.postings[variant]
                idf = math.log(1 + (n - len(posting) + 0.5) / (len(posting) + 0.5))
                # A prefix completion counts a little less than the exact word.
                boost = 1.0 if variant == term else 0.8
                for doc, tf in posting.items():
                    norm = tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lengths[doc] / self.avg_len))
                    scores[doc] = scores.get(doc, 0.0) + boost * idf * norm
                    matched.setdefault(doc, set()).add(idx)
        # Coordination: a doc matching every query word beats one matching a single word.
        ranked = []
        for doc, score in scores.items():
            coverage = len(matched[doc]) / len(terms)
            ranked.append((score * (0.35 + 0.65 * coverage), coverage, doc))
        ranked.sort(key=lambda r: r[0], reverse=True)
        return [(self.docs[doc], score, coverage) for score, coverage, doc in ranked]


def _phrase_in(phrase, query_terms, query_text):
    """True when every word of `phrase` appears in the query (last word may be partial)."""
    words = tokenize(phrase)
    if not words:
        return False
    for w in words:
        if w in query_terms:
            continue
        # allow the word being typed right now to be a prefix of the phrase word
        last = query_terms[-1] if query_terms else ""
        if not (query_text and not query_text.endswith(" ") and len(last) >= 3 and w.startswith(last)):
            return False
    return True


class LocalSearch:
    def __init__(self, path=CORPUS_PATH):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.web = data.get("web", [])
        self.knowledge = data.get("knowledge", [])
        self.answers = data.get("answers", [])
        self.news = data.get("news", [])
        self.videos = data.get("videos", [])
        self.web_index = Bm25Index(self.web, {"title": 3, "tags": 2, "snippet": 1})
        self.question_index = Bm25Index(data.get("questions", []), {"question": 2, "snippet": 1})
        self.news_index = Bm25Index(self.news, {"title": 3, "tags": 2, "snippet": 1})
        self.video_index = Bm25Index(self.videos, {"title": 3, "tags": 2, "channel": 1})

    # ---- public ------------------------------------------------------------

    def search(self, kind, q, page):
        empty = {"answerBox": None, "knowledge": None, "results": [], "images": [],
                 "news": [], "videos": [], "questions": [], "relatedSearches": []}
        if kind == "images":
            return {**empty, "images": self._images(q, page)}
        if kind == "news":
            return {**empty, "news": self._news(q, page)}
        if kind == "videos":
            return {**empty, "videos": self._videos(q, page)}
        return {**empty, **self._web(q, page)}

    # ---- per tab -----------------------------------------------------------

    def _relevant(self, hits, query):
        # Short queries must match at least half their words; longer ones
        # may match a quarter (still ranked below fuller matches).
        min_coverage = 0.5 if len(tokenize(query)) <= 2 else 0.25
        return [h for h in hits if h[2] >= min_coverage]

    def _page(self, items, page):
        start = (page - 1) * PAGE_SIZE
        return items[start:start + PAGE_SIZE]

    def _web(self, q, page):
        hits = self._relevant(self.web_index.search(q), q)
        results = []
        for pos, (doc, _score, _cov) in enumerate(self._page(hits, page), start=(page - 1) * PAGE_SIZE + 1):
            results.append({
                "position": pos,
                "title": doc["title"],
                "link": doc["link"],
                "displayLink": display_link(doc["link"]),
                "snippet": doc.get("snippet"),
                "date": doc.get("date"),
                "sitelinks": doc.get("sitelinks", []),
            })

        terms = tokenize(q)
        answer = None
        for a in self.answers:
            if all(_phrase_in(p, terms, q) for p in a["match"]):
                answer = {"title": a.get("title"), "answer": a.get("answer"),
                          "snippet": a.get("snippet"), "link": a.get("link")}
                break

        knowledge = None
        # Most specific entity wins ("chennai metro" beats "chennai").
        for k in sorted(self.knowledge, key=lambda k: -max(len(m) for m in k["match"])):
            if any(_phrase_in(m, terms, q) for m in k["match"]):
                knowledge = {
                    "title": k["title"], "type": k.get("type"), "description": k.get("description"),
                    "imageUrl": thumb_url(k["title"]), "attributes": k.get("attributes", {}),
                }
                break

        questions = [
            {"question": d["question"], "snippet": d.get("snippet"), "link": d.get("link")}
            for d, _s, cov in self.question_index.search(q) if cov >= 0.5
        ][:4]

        related, seen = [], {q.strip().lower()}
        for doc, _s, _c in hits[:6]:
            for tag in doc.get("tags", []):
                if tag.lower() not in seen:
                    seen.add(tag.lower())
                    related.append(tag)
        return {
            "results": results,
            "answerBox": answer if page == 1 else None,
            "knowledge": knowledge if page == 1 else None,
            "questions": questions if page == 1 else [],
            "relatedSearches": related[:8],
        }

    def _images(self, q, page):
        hits = self._relevant(self.web_index.search(q), q)
        images = []
        for doc, _s, _c in hits:
            images.append({
                "title": doc["title"],
                "thumbnailUrl": thumb_url(doc["title"]),
                "imageUrl": thumb_url(doc["title"]),
                "source": display_link(doc["link"]),
                "link": doc["link"],
            })
        return images[(page - 1) * 20:page * 20]

    def _news(self, q, page):
        hits = self._relevant(self.news_index.search(q), q)
        return [{
            "title": d["title"], "link": d["link"], "snippet": d.get("snippet"),
            "date": d.get("date"), "source": d.get("source"), "imageUrl": thumb_url(d["title"]),
        } for d, _s, _c in self._page(hits, page)]

    def _videos(self, q, page):
        hits = self._relevant(self.video_index.search(q), q)
        return [{
            "title": d["title"], "link": d["link"], "snippet": d.get("snippet"),
            "imageUrl": thumb_url(d["title"], "video"), "duration": d.get("duration"),
            "channel": d.get("channel"),
        } for d, _s, _c in self._page(hits, page)]


# ---- SVG thumbnails ---------------------------------------------------------

PALETTES = [
    ("#ff9f5a", "#e93d9a"), ("#533afd", "#7fd3ff"), ("#e93d9a", "#533afd"),
    ("#1c1e54", "#533afd"), ("#ffa25f", "#ea2261"), ("#7fd3ff", "#b9a8ff"),
    ("#0d253d", "#2743f5"), ("#f45fd9", "#ffa25f"),
]


def _escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def render_thumb(title, kind="image"):
    """Deterministic gradient tile with the title set in it — no network needed."""
    digest = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16)
    c1, c2 = PALETTES[digest % len(PALETTES)]
    width, height = (640, 360) if kind == "video" else (480, [360, 480, 320, 420][digest % 4])

    words, lines, line = title.split(), [], ""
    for w in words:
        if len(line) + len(w) + 1 > 22:
            lines.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        lines.append(line)
    lines = lines[:4]
    text = "".join(
        f'<text x="32" y="{height - 40 - (len(lines) - 1 - i) * 34}" font-family="Inter, system-ui, sans-serif" '
        f'font-size="26" font-weight="600" fill="#fff">{_escape(l)}</text>'
        for i, l in enumerate(lines)
    )
    angle = digest % 360
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        f'<defs><linearGradient id="g" gradientTransform="rotate({angle % 90} .5 .5)">'
        f'<stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>'
        f'<linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset=".35" stop-color="#000" stop-opacity="0"/>'
        f'<stop offset="1" stop-color="#000" stop-opacity=".45"/></linearGradient></defs>'
        f'<rect width="100%" height="100%" fill="url(#g)"/>'
        f'<circle cx="{width * 0.8:.0f}" cy="{height * 0.25:.0f}" r="{height * 0.35:.0f}" fill="#fff" opacity=".12"/>'
        f'<rect width="100%" height="100%" fill="url(#s)"/>{text}</svg>'
    )
