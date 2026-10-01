"""
Seekr — FastAPI backend.

    GET /api/search?q=&type=web|images|news|videos&page=1
    GET /api/health
    GET /api/thumb?t=title&kind=image|video   (local SVG tiles)

Search provider (SEARCH_PROVIDER in backend/.env):
    auto    live Google results through Serper when SERPER_API_KEY is set,
            falling back to the bundled local index if Serper can't answer
            (offline, out of credits, bad key). The default.
    serper  Serper only — failures are shown to the user as an error card.
    local   the bundled local index only; never touches the network.

In production (after `npm run build`) this app also serves frontend/dist,
so one process on one port is the whole site.
"""
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from local_search import LocalSearch, render_thumb
from serper_search import SerperError, SerperSearch


def load_dotenv(path):
    """Tiny .env reader so there's no extra dependency just for this."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

VALID_TYPES = {"web", "images", "news", "videos"}
CACHE_TTL_SECONDS = 5 * 60
CACHE_MAX_ENTRIES = 500

serper_key = os.environ.get("SERPER_API_KEY", "").strip()
PROVIDER_MODE = os.environ.get("SEARCH_PROVIDER", "auto").strip().lower()
if PROVIDER_MODE not in {"auto", "serper", "local"}:
    PROVIDER_MODE = "auto"
if not serper_key:
    PROVIDER_MODE = "local"

local = LocalSearch()
serper = SerperSearch(serper_key) if serper_key else None

app = FastAPI(title="Seekr", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class ApiError(Exception):
    def __init__(self, status, message, hint=None):
        super().__init__(message)
        self.status, self.message, self.hint = status, message, hint


def error_body(status, message, hint=None):
    return JSONResponse(status_code=status, content={"message": message, "hint": hint, "status": status})


@app.exception_handler(ApiError)
async def handle_api_error(_request: Request, e: ApiError):
    return error_body(e.status, e.message, e.hint)


@app.exception_handler(RequestValidationError)
async def handle_validation(_request: Request, _e: RequestValidationError):
    return error_body(400, "Enter something to search for.")


@app.exception_handler(SerperError)
async def handle_serper(_request: Request, e: SerperError):
    if e.kind == "auth":
        return error_body(502, "Seekr can't reach the search index.",
                          "The SERPER_API_KEY on the server is missing or invalid.")
    if e.kind == "rate_limit":
        return error_body(502, "Seekr is out of search credits for now.",
                          "Try again later, or top up the Serper account.")
    return error_body(504, "The search took too long to come back.", "Try that query again.")


# ---- cache: honest `cached` flag, same as the original ---------------------

_cache = {}


def cache_get(key):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _cache.pop(key, None)
    return None


def cache_put(key, value):
    if len(_cache) >= CACHE_MAX_ENTRIES:
        _cache.pop(next(iter(_cache)))  # drop the oldest entry
    _cache[key] = (time.monotonic() + CACHE_TTL_SECONDS, value)


# ---- routes ----------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "up", "provider": PROVIDER_MODE}


@app.get("/api/search")
def search(q: str = "", type: str = "web", page: int = 1):
    query = q.strip()
    if not query or len(query) > 256:
        raise ApiError(400, "Enter something to search for.")
    if type not in VALID_TYPES:
        raise ApiError(400, "That search tab doesn't exist.", "Use web, images, news or videos.")
    page = max(1, min(page, 10))

    key = f"{type}:{query.lower()}:{page}"
    hit = cache_get(key)
    if hit:
        return {**hit, "cached": True}

    start = time.perf_counter()
    source = "local"
    if PROVIDER_MODE == "local":
        body = local.search(type, query, page)
    elif PROVIDER_MODE == "serper":
        body, source = serper.search(type, query, page), "serper"
    else:
        try:
            body, source = serper.search(type, query, page), "serper"
        except SerperError:
            body = local.search(type, query, page)  # keep working offline
    took_ms = int((time.perf_counter() - start) * 1000)

    response = {"query": query, "type": type, "page": page, "tookMs": took_ms, "cached": False,
                "source": source, **body}
    # Fallback answers aren't cached, so live results return as soon as Serper does.
    if source == "serper" or PROVIDER_MODE == "local":
        cache_put(key, response)
    return response


@app.get("/api/thumb")
def thumb(t: str = "Seekr", kind: str = "image"):
    svg = render_thumb(t[:120], "video" if kind == "video" else "image")
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


# ---- built frontend (production) --------------------------------------------

DIST = Path(os.environ.get("FRONTEND_DIST", BASE_DIR.parent / "frontend" / "dist")).resolve()

if DIST.exists():
    if (DIST / "assets").exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return error_body(404, "That endpoint doesn't exist.")
        file = (DIST / path).resolve()
        if path and file.is_file() and DIST in file.parents:
            return FileResponse(file)
        return FileResponse(DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
