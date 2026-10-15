"""Android/mobile API for the original PlotWatch 4.1 crawler.

Auth: shared pilot token over HTTPS; do not expose Serper key to clients.
Scheduler: one process / one replica, persistent SQLite volume.
"""
from __future__ import annotations
import hmac
import os
from contextlib import asynccontextmanager
from typing import Annotated
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from threading import Thread, Event
import logging
from dotenv import load_dotenv
from pathlib import Path
from engine import PlotQuery
from database import add_watch, watches, get_watch, findings, checks, discovered_links, set_enabled
from jobs import init_jobs, queue_watch, read_job, process_next_job

load_dotenv(Path(__file__).resolve().parent / '.env')

stop_signal = Event()
worker_thread = None

def worker_loop():
    while not stop_signal.is_set():
        try:
            process_next_job()
        except Exception:
            logging.exception("Background worker error")
        stop_signal.wait(20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global worker_thread
    app_token = os.getenv('PLOTWATCH_APP_TOKEN', '').strip()
    if len(app_token) < 24 or app_token.startswith('change_'):
        raise RuntimeError('PLOTWATCH_APP_TOKEN must be a long random token (24+ characters), not a placeholder.')
    init_jobs()
    stop_signal.clear()
    if os.getenv('DISABLE_SCHEDULER', '').lower() not in {'1', 'true'}:
        worker_thread = Thread(target=worker_loop, name='PlotWatchCrawlerWorker', daemon=True)
        worker_thread.start()
    try:
        yield
    finally:
        stop_signal.set()
        if worker_thread:
            worker_thread.join(timeout=2)
            worker_thread = None


app = FastAPI(
    title="Pakistan PlotWatch API",
    version="5.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)


bearer_scheme = HTTPBearer(auto_error=False)


def auth(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]):
    expected = os.getenv("PLOTWATCH_APP_TOKEN", "").strip()
    supplied = credentials.credentials.strip() if credentials is not None else ""
    if not expected or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing access token")
    return True


class PlotRequest(BaseModel):
    plot_number: str = Field(min_length=1, max_length=40)
    city: str = Field(min_length=1, max_length=80)
    society: str = Field(min_length=1, max_length=120)
    block: str = Field(default='', max_length=80)


class ToggleRequest(BaseModel):
    enabled: bool


@app.get('/health')
def health():
    return {'status': 'ok', 'service': 'Pakistan PlotWatch'}


@app.get('/api/watches', dependencies=[Depends(auth)])
def list_watches():
    return {'items': watches()}


@app.post('/api/watches', status_code=202, dependencies=[Depends(auth)])
def create_watch(payload: PlotRequest):
    query = PlotQuery(payload.plot_number.strip(), payload.city.strip(), payload.society.strip(), payload.block.strip())
    try:
        query.validate()
        watch_id = add_watch(query, mode='hybrid', seed_urls='', domains=(), max_pages=8)
        job_id = queue_watch(watch_id)
    except ValueError as ex:
        raise HTTPException(status_code=422, detail=str(ex))
    return {'watch_id': watch_id, 'job_id': job_id, 'status': 'queued'}


@app.get('/api/jobs/{job_id}', dependencies=[Depends(auth)])
def get_job(job_id: int):
    job = read_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='Job not found')
    return job


@app.get('/api/watches/{watch_id}', dependencies=[Depends(auth)])
def watch_details(watch_id: int):
    watch = get_watch(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail='Plot watch not found')
    return {'watch': watch, 'findings': findings(watch_id),
            'discovered_links': discovered_links(watch_id)}


@app.post('/api/watches/{watch_id}/refresh', status_code=202, dependencies=[Depends(auth)])
def refresh(watch_id: int):
    if not get_watch(watch_id):
        raise HTTPException(status_code=404, detail='Plot watch not found')
    return {'watch_id': watch_id, 'job_id': queue_watch(watch_id), 'status': 'queued'}


@app.patch('/api/watches/{watch_id}', dependencies=[Depends(auth)])
def toggle(watch_id: int, payload: ToggleRequest):
    if not get_watch(watch_id):
        raise HTTPException(status_code=404, detail='Plot watch not found')
    set_enabled(watch_id, payload.enabled)
    return {'watch_id': watch_id, 'enabled': payload.enabled}


@app.get('/api/history', dependencies=[Depends(auth)])
def history():
    return {'items': checks()}
