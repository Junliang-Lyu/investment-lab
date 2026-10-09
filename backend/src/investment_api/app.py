"""FastAPI app. Run: uvicorn investment_api.app:app --port 8081"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .lab import build_router
from .ratelimit import RateLimiter
from .settings import Settings


def create_app(settings: Settings | None = None, client_factory=None, provider_factory=None,
               macro_fetch=None) -> FastAPI:
    settings = settings or Settings.from_env()
    docs = settings.api_docs
    app = FastAPI(title="Investment Lab API", docs_url="/api/docs" if docs else None,
                  redoc_url=None, openapi_url="/api/openapi.json" if docs else None)
    limiter = RateLimiter(settings.rate_per_minute)

    if client_factory is None:
        from investment_data.config import load_env_file
        from investment_data.edgar import EdgarClient
        load_env_file()
        client_factory = lambda: EdgarClient(cache_dir=settings.cache_dir)  # noqa: E731

    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.allowed_origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type"])

    @app.middleware("http")
    async def guard(request: Request, call_next):
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
        if request.url.path.startswith("/api/lab") and not limiter.allow(ip):
            return JSONResponse({"detail": "too many requests"}, status_code=429)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Cache-Control"] = "no-store" if request.method != "GET" else resp.headers.get("Cache-Control", "no-cache")
        return resp

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    app.include_router(build_router(settings, client_factory, provider_factory, macro_fetch))
    return app


def __getattr__(name):  # lazy default app for uvicorn: investment_api.app:app
    if name == "app":
        return create_app()
    raise AttributeError(name)
