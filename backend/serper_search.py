"""
Optional live provider: real Google results through the Serper API.

Used only when SERPER_API_KEY is set. Mirrors the original Seekr backend:
POST https://google.serper.dev/{search|images|news|videos} with a JSON body,
then hand-map every optional block so a missing field never crashes.
"""
import json
import socket
import urllib.error
import urllib.request

from local_search import display_link

ENDPOINTS = {"web": "search", "images": "images", "news": "news", "videos": "videos"}


class SerperError(Exception):
    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind  # auth | rate_limit | timeout


def _text(node, field):
    value = (node or {}).get(field)
    if value is None:
        return None
    value = str(value)
    return value if value.strip() else None


class SerperSearch:
    def __init__(self, api_key, base_url="https://google.serper.dev", country="in", language="en"):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.country, self.language = country, language

    def _call(self, kind, q, page):
        body = json.dumps({"q": q, "gl": self.country, "hl": self.language, "num": 10, "page": page}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/{ENDPOINTS[kind]}",
            data=body,
            method="POST",
            headers={"X-API-KEY": self.api_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SerperError("auth") from e
            if e.code in (402, 429):
                raise SerperError("rate_limit") from e
            raise SerperError("timeout") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
            raise SerperError("timeout") from e

    def search(self, kind, q, page):
        root = self._call(kind, q, page)

        results = []
        for i, n in enumerate(root.get("organic") or [], start=1):
            link = _text(n, "link")
            results.append({
                "position": n.get("position", i), "title": _text(n, "title"), "link": link,
                "displayLink": display_link(link) if link else None, "snippet": _text(n, "snippet"),
                "date": _text(n, "date"),
                "sitelinks": [{"title": _text(s, "title"), "link": _text(s, "link")} for s in n.get("sitelinks") or []],
            })

        ab = root.get("answerBox")
        answer = {"title": _text(ab, "title"), "answer": _text(ab, "answer"),
                  "snippet": _text(ab, "snippet"), "link": _text(ab, "link")} if ab else None

        kg = root.get("knowledgeGraph")
        knowledge = None
        if kg:
            attrs = {k: str(v) for k, v in (kg.get("attributes") or {}).items() if v is not None}
            knowledge = {"title": _text(kg, "title"), "type": _text(kg, "type"),
                         "description": _text(kg, "description"),
                         "imageUrl": _text(kg, "imageUrl") or _text(kg, "image"), "attributes": attrs}

        return {
            "answerBox": answer,
            "knowledge": knowledge,
            "results": results,
            "images": [{"title": _text(n, "title"), "thumbnailUrl": _text(n, "thumbnailUrl"),
                        "imageUrl": _text(n, "imageUrl"), "source": _text(n, "source"),
                        "link": _text(n, "link")} for n in root.get("images") or []],
            "news": [{"title": _text(n, "title"), "link": _text(n, "link"), "snippet": _text(n, "snippet"),
                      "date": _text(n, "date"), "source": _text(n, "source"),
                      "imageUrl": _text(n, "imageUrl")} for n in root.get("news") or []],
            "videos": [{"title": _text(n, "title"), "link": _text(n, "link"), "snippet": _text(n, "snippet"),
                        "imageUrl": _text(n, "imageUrl"), "duration": _text(n, "duration"),
                        "channel": _text(n, "channel")} for n in root.get("videos") or []],
            "questions": [{"question": _text(n, "question"), "snippet": _text(n, "snippet"),
                           "link": _text(n, "link")} for n in root.get("peopleAlsoAsk") or []],
            "relatedSearches": [n["query"] for n in root.get("relatedSearches") or [] if n.get("query")],
        }
