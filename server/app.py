import asyncio
import hashlib
import json
import logging
import secrets
import time
import unicodedata
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress

import httpx
import anyio

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import engine
from .conversation import remember, resolve
from .config import Settings
from .persona import ROOT, load_persona
from .retrieval import KnowledgeIndex
from .guardrails import Guardrails, GuardBlocked, GuardUnavailable


class BufferedResponse(StreamingResponse):
    async def stream_response(self, send):
        try:
            await super().stream_response(send)
        finally:
            # A disconnect during ASGI send can leave the generator suspended at
            # a status yield. Close it explicitly so its pending task is cancelled.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    owner = load_persona(settings.persona_path)
    index = KnowledgeIndex(owner)
    guards = Guardrails(settings)

    @asynccontextmanager
    async def lifespan(app):
        async def sweep():
            while True:
                await asyncio.sleep(60)
                cleanup()
        task = asyncio.create_task(sweep())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Personal Persona Chatbot", lifespan=lifespan)
    app.state.persona = owner
    app.state.index = index
    app.state.guardrails = guards
    sessions = {}
    limits = defaultdict(deque)
    salt = secrets.token_bytes(32)
    inference_lock = asyncio.Lock()
    active = set()
    logger = logging.getLogger("uvicorn.error.portfolio")
    app.state.sessions = sessions
    app.state.limits = limits
    app.state.active = active
    app.state.inference_lock = inference_lock


    class Query(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        message: str = Field(min_length=1, max_length=1000)


    def cleanup():
        now = time.monotonic()
        for key in list(sessions):
            if sessions[key]["expires"] < now and key not in active:
                del sessions[key]
        for key in list(limits):
            while limits[key] and limits[key][0] < now - 60:
                limits[key].popleft()
            if not limits[key]:
                del limits[key]


    def check_limit(key, count):
        if len(limits[key]) >= count:
            raise HTTPException(429, "요청이 많아요. 잠시 후 다시 시도해 주세요.", headers={"Retry-After": "60"})
        limits[key].append(time.monotonic())


    def ip_key(request):
        host = request.client.host if request.client else "unknown"
        return hashlib.sha256(salt + host.encode()).hexdigest()


    @app.middleware("http")
    async def protection(request, call_next):
        if request.method in {"POST", "DELETE"}:
            origin = request.headers.get("origin")
            allowed = settings.public_origin or str(request.base_url).rstrip("/")
            if origin and origin != allowed:
                return JSONResponse({"detail": "Origin not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/" or request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-cache"
        return response


    @app.post("/api/session")
    async def create_session(request: Request):
        cleanup()
        check_limit("issue:" + ip_key(request), 20)
        sid = request.cookies.get("session")
        if sid not in sessions:
            if len(sessions) >= 1000:
                raise HTTPException(503, "Session capacity reached")
            sid = secrets.token_urlsafe(32)
            sessions[sid] = {"expires": time.monotonic() + 1800, "history": [], "sources": [], "topics": []}
        response = JSONResponse({"ok": True, "fictional": owner.fictional})
        response.set_cookie("session", sid, httponly=True, secure=settings.cookie_secure, samesite="strict")
        return response


    @app.delete("/api/session")
    async def delete_session(request: Request):
        sid = request.cookies.get("session")
        sessions.pop(sid, None)
        response = JSONResponse({"ok": True})
        response.delete_cookie("session")
        return response

    @app.get("/api/session")
    async def get_session(request: Request):
        cleanup()
        state = sessions.get(request.cookies.get("session"))
        if state is None:
            raise HTTPException(401, "세션이 만료되었어요.")
        return {"messages": state["history"]}


    @app.get("/api/persona")
    async def persona():
        return owner.presentation()


    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    readiness = {"at": float("-inf"), "ok": False}
    readiness_lock = asyncio.Lock()

    @app.get("/health/ready")
    async def ready():
        async with readiness_lock:
            if time.monotonic() - readiness["at"] > 10:
                try:
                    async with asyncio.timeout(3), httpx.AsyncClient(timeout=3, trust_env=False) as client:
                        response = await client.post(settings.ollama_url + "/api/show", json={"model": settings.ollama_model})
                        response.raise_for_status()
                        capabilities = response.json().get("capabilities", [])
                        readiness["ok"] = "completion" in capabilities
                except (httpx.HTTPError, ValueError, TimeoutError):
                    readiness["ok"] = False
                readiness["at"] = time.monotonic()
        return JSONResponse({"status": "ready" if readiness["ok"] else "unavailable"},
                            status_code=200 if readiness["ok"] else 503,
                            headers={"Cache-Control": "no-store"})


    def event(name, request_id, **payload):
        return f"event: {name}\ndata: {json.dumps({'request_id': request_id, **payload}, ensure_ascii=False)}\n\n"


    async def infer(query, state, progress):
        """Return only a fully validated answer or a fixed fallback."""
        def fallback(reason):
            return engine.FALLBACKS[reason], [], "fallback", reason

        try:
            guards.check_patterns(query, "input")
            if engine.classify(query) == "greeting":
                return f"안녕하세요! {owner.name}의 AI 포트폴리오 안내자예요. 프로젝트와 일하는 방식에 대해 물어보세요.", [], "static", None
            try:
                await asyncio.wait_for(inference_lock.acquire(), timeout=5)
            except TimeoutError:
                return fallback("busy")
            try:
                async with asyncio.timeout(settings.pipeline_timeout):
                    await guards.check_input(query, state["history"])
                    progress.put_nowait("retrieving")
                    focus, clarification = resolve(query, state["topics"], index, owner)
                    docs = [index.by_id[i] for i in focus[:3]] if focus else index.retrieve(query)
                    if clarification:
                        return clarification, [], "clarification", None
                    if not docs:
                        return fallback("no_context")
                    progress.put_nowait("checking_context")
                    await guards.check_context(docs)
                    progress.put_nowait("generating")
                    try:
                        raw = await engine.generate(query, docs, state["history"], persona=owner, settings=settings)
                    except (ValueError, KeyError):
                        return fallback("invalid_output")
                    except Exception:
                        return fallback("model_unavailable")
                    progress.put_nowait("validating")
                    try:
                        text, sources = engine.validate_answer(raw, docs)
                    except (ValueError, KeyError):
                        return fallback("invalid_output")
                    progress.put_nowait("checking_output")
                    await guards.check_output(query, text)
                    return text, sources, "answer", None
            finally:
                inference_lock.release()
        except GuardBlocked as blocked:
            return fallback(blocked.reason)
        except (GuardUnavailable, TimeoutError):
            return fallback("guard_unavailable")


    @app.post("/api/chat")
    async def chat(request: Request):
        cleanup()
        sid = request.cookies.get("session")
        if sid not in sessions:
            raise HTTPException(401, "세션이 만료되었어요. 새 대화를 시작해 주세요.")
        check_limit("ip:" + ip_key(request), 30)
        check_limit("session:" + sid, 10)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 16384:
                raise HTTPException(413, "질문이 너무 길어요.")
        try:
            query = Query.model_validate_json(bytes(data)).message
        except ValidationError:
            raise HTTPException(400, "1~1,000자의 질문을 입력해 주세요.") from None
        query = unicodedata.normalize("NFC", query)
        query = " ".join("".join(c for c in query if not unicodedata.category(c).startswith("C") or c in "\n\t").split())
        if not query or len(query) > 1000:
            raise HTTPException(400, "질문을 입력해 주세요.")
        if sid in active:
            raise HTTPException(429, "진행 중인 답변을 기다려 주세요.")
        if len(active) >= 5:
            raise HTTPException(503, "서버가 혼잡해요. 잠시 후 다시 시도해 주세요.")
        active.add(sid)
        state = sessions[sid]
        state["expires"] = time.monotonic() + 1800
        request_id = secrets.token_hex(8)

        async def stream():
            started = time.monotonic()
            pending = None
            reason, status, sources = None, "answer", []
            try:
                yield event("status", request_id, phase="checking_input")
                progress = asyncio.Queue()
                pending = asyncio.create_task(infer(query, state, progress))
                # Wake on either a phase change, task completion or heartbeat.
                while not pending.done() or not progress.empty():
                    if not progress.empty():
                        yield event("status", request_id, phase=progress.get_nowait())
                        continue
                    phase_task = asyncio.create_task(progress.get())
                    try:
                        done, _ = await asyncio.wait({pending, phase_task}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
                        if phase_task in done:
                            yield event("status", request_id, phase=phase_task.result())
                        elif not done:
                            yield ": heartbeat\n\n"
                    finally:
                        if not phase_task.done():
                            phase_task.cancel()
                            with suppress(asyncio.CancelledError):
                                await phase_task
                text, sources, status, reason = pending.result()
                # Only validated text or fixed templates can cross this boundary.
                for seq, offset in enumerate(range(0, len(text), 16)):
                    yield event("delta", request_id, seq=seq, text=text[offset:offset+16])
                    await asyncio.sleep(.02)
                # Do not remember a draft that was cancelled before delivery finished.
                if sid in sessions and (status in {"answer", "static", "clarification"} or reason == "no_context"):
                    remember(state, query, text)
                    if status == "answer":
                        state["sources"] = [s["id"] for s in sources]
                        state["topics"] = [s["id"] for s in sources]
                yield event("done", request_id, status=status, sources=sources, reason_code=reason)
            except asyncio.CancelledError:
                raise
            except Exception:
                yield event("error", request_id, code="internal_error", retryable=True)
            finally:
                if pending and not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                active.discard(sid)
                logger.info(json.dumps({"request_id": request_id, "status": status, "reason": reason, "duration_ms": round((time.monotonic()-started)*1000)}))

        return BufferedResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")


    @app.get("/")
    async def homepage():
        return FileResponse(ROOT / "web/index.html")

    return app


app = create_app()
