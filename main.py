import asyncio
import json
import queue
import threading
import uuid
import os

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from typing import Optional

from agent.solver import solve_issue
from database import init_db
import auth as auth_service

# ── Startup ───────────────────────────────────────────────────────────────────
init_db()

app = FastAPI(title="GitHub Issue Solver")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store  {job_id: queue.Queue}
jobs: dict = {}


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """Strict auth — raises 401 if token missing/invalid."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = authorization[7:]
    user_id = auth_service.verify_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Token expired or invalid")
    user = auth_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def get_optional_user(authorization: Optional[str] = Header(None)) -> Optional[dict]:
    """Optional auth — returns None if no/bad token."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    user_id = auth_service.verify_token(token)
    if not user_id:
        return None
    return auth_service.get_user(user_id)


# ── Request / Response models ─────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    name: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class SolveRequest(BaseModel):
    issue_url: str


# ── Static routes ─────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse("index.html")


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.post("/auth/register")
async def register(req: RegisterRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    if len(req.name.strip()) < 2:
        raise HTTPException(status_code=400, detail="Name must be at least 2 characters")
    user = auth_service.register_user(req.email, req.name, req.password)
    if not user:
        raise HTTPException(status_code=409, detail="Email already registered")
    token = auth_service.create_token(user["id"])
    return {"token": token, "user": user}


@app.post("/auth/login")
async def login(req: LoginRequest):
    user = auth_service.login_user(req.email, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = auth_service.create_token(user["id"])
    return {"token": token, "user": user}


@app.get("/auth/me")
async def me(current_user: dict = Depends(get_current_user)):
    return current_user


# ── History routes ────────────────────────────────────────────────────────────

@app.get("/history")
async def history(current_user: dict = Depends(get_current_user)):
    sessions = auth_service.get_user_sessions(current_user["id"])
    return {"sessions": sessions}


@app.get("/sessions/{session_id}/events")
async def session_events(session_id: str, current_user: dict = Depends(get_current_user)):
    events = auth_service.get_session_events(session_id, current_user["id"])
    if events is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"events": events}


# ── Solve route ───────────────────────────────────────────────────────────────

@app.post("/solve")
async def solve(request: SolveRequest, current_user: Optional[dict] = Depends(get_optional_user)):
    job_id = str(uuid.uuid4())
    q = queue.Queue()
    jobs[job_id] = q

    user_id = current_user["id"] if current_user else None

    # Create a DB session record if user is authenticated
    if user_id:
        auth_service.create_session(user_id, request.issue_url, job_id)

    def run():
        try:
            solve_issue(request.issue_url, q, session_id=job_id if user_id else None)
        except Exception as e:
            q.put({"type": "error", "message": str(e)})
        finally:
            q.put({"type": "done"})

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    return {"job_id": job_id}


# ── Stream route ──────────────────────────────────────────────────────────────

@app.get("/stream/{job_id}")
async def stream(job_id: str, current_user: Optional[dict] = Depends(get_optional_user)):
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    q = jobs[job_id]
    loop = asyncio.get_event_loop()
    user_id = current_user["id"] if current_user else None

    async def event_generator():
        while True:
            try:
                item = await loop.run_in_executor(None, lambda: q.get(timeout=120))

                # Persist event to DB if session is tracked
                if user_id:
                    try:
                        extra = {k: v for k, v in item.items() if k not in ("type", "message")}
                        auth_service.save_event(
                            session_id=job_id,
                            event_type=item.get("type", ""),
                            message=item.get("message"),
                            extra_data=extra if extra else None,
                        )
                        # Update session metadata on first info event with issue details
                        if item.get("type") == "info" and item.get("issue_number"):
                            auth_service.update_session_meta(
                                job_id,
                                item.get("issue_title", ""),
                                item.get("issue_number"),
                                item.get("repo_name", ""),
                            )
                        # Mark session done/error
                        if item.get("type") == "done":
                            auth_service.complete_session(job_id, "done")
                        elif item.get("type") == "error":
                            auth_service.complete_session(job_id, "error")
                    except Exception:
                        pass  # Never let DB errors break the stream

                yield f"data: {json.dumps(item)}\n\n"

                if item.get("type") in ("done", "error"):
                    jobs.pop(job_id, None)
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
