"""A read-only localhost view of what the rubrics decided.

Thin by design: every judgement lives in `views.py`, which is pure. This module
opens a read-only connection per request, runs one query, and renders.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..discovery.store import SCHEMA_VERSION, Store
from ..matching.search_config import load_search_config
from . import actions
from .views import MAX_SCORE, build_page

HERE = Path(__file__).parent

#: One query, one join. `action` is deliberately NOT selected - the dashboard
#: derives it so the page fills in live (spec section 4).
ROLE_QUERY = """
SELECT p.id, p.company, p.company_normalised, p.role_title, p.location,
       p.apply_url, p.role_fit, p.role_discipline, p.hybrid_test,
       p.hybrid_evidence, p.role_overlaps, p.role_gaps, p.role_reason,
       p.seniority_note, p.role_scored_at, p.reach, p.domain_fit,
       c.company_fit
FROM postings p
LEFT JOIN companies c
  ON c.company_normalised = p.company_normalised AND c.scored_at != ''
WHERE p.status != 'rejected' AND p.closed_at IS NULL
"""

_JSON_COLUMNS = ("hybrid_evidence", "role_overlaps", "role_gaps")


def _rows(db_path: Path) -> list[dict]:
    with Store(db_path, read_only=True) as store:
        raw = store._conn.execute(ROLE_QUERY).fetchall()
    out = []
    for r in raw:
        d = dict(r)
        for col in _JSON_COLUMNS:
            d[col] = json.loads(d.get(col) or "[]")
        out.append(d)
    return out


def create_app(db_path: Path) -> FastAPI:
    db_path = Path(db_path)
    # Fail at startup, not as an empty page that reads like "no results".
    with Store(db_path, read_only=True) as probe:
        version = probe.schema_version()
    if version != SCHEMA_VERSION:
        raise RuntimeError(
            f"{db_path} is at schema v{version}, this code expects "
            f"v{SCHEMA_VERSION}. The dashboard is read-only and will not "
            "migrate a database a CLI may be writing to. Run "
            "`python -m jobhunt.discovery --status` first, which migrates.")

    metros = load_search_config().home_metros
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    app = FastAPI(title="jobhunt")
    app.mount("/static", StaticFiles(directory=str(HERE / "static")),
              name="static")

    def _app_state_for(company: str, role_title: str | None) -> tuple[str, str]:
        out = actions.application_dir(company, role_title)
        return out.name, actions.application_state(out)

    def _page(include_app_state: bool = True):
        with Store(db_path, read_only=True) as store:
            prefiltered = store.count_prefiltered()
        # The header partial only ever renders bucket counts, never a role
        # card, so it has no use for app_state_for - and it polls every 10s,
        # which would otherwise mean a stat() pair per scored role every 10
        # seconds for no reason.
        app_state_for = _app_state_for if include_app_state else None
        return build_page(_rows(db_path), metros, prefiltered,
                          app_state_for=app_state_for)

    def _resolve_active(page, action: str | None) -> str | None:
        known = {b.action for b in page.buckets}
        # An unknown value shows everything. A typo'd or stale URL must not
        # render as "no results", which is indistinguishable from a bug.
        return action if action in known else None

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, action: str | None = None):
        page = _page()
        active = _resolve_active(page, action)
        return templates.TemplateResponse(
            request, "page.html",
            {"page": page, "max_score": MAX_SCORE, "active": active})

    @app.get("/partials/header", response_class=HTMLResponse)
    def header(request: Request, action: str | None = None):
        page = _page(include_app_state=False)
        active = _resolve_active(page, action)
        return templates.TemplateResponse(
            request, "_header.html",
            {"page": page, "max_score": MAX_SCORE, "active": active})

    @app.post("/generate/{posting_id:path}", response_class=HTMLResponse)
    def generate(request: Request, posting_id: str):
        with Store(db_path, read_only=True) as store:
            posting = store.get(posting_id)
            verdict = store.role_verdict_of(posting_id)
        if posting is None or verdict is None:
            raise HTTPException(404, "no scored posting with that id")

        out = actions.application_dir(posting.company, verdict.role_title)
        ctx = {"request": request, "posting_id": posting_id,
               "dirname": out.name}
        if actions.application_state(out) == "ready":
            return templates.TemplateResponse(request, "_docbuttons.html",
                                              {**ctx, "state": "ready"})

        # CRITICAL-2 (review round 1): the CLI's own `existing` check is no
        # backstop here, because the first request only writes its files
        # AFTER both API calls finish - a second request arriving mid-flight
        # would pass that same check. An in-process lock, acquired
        # non-blockingly, is the actual guard: a concurrent request sees it
        # held and reports "generating" instead of calling run_generation
        # again. See `actions.py`'s module docstring for why this is a lock
        # and not a directory.
        lock = actions.lock_for(posting_id)
        if not lock.acquire(blocking=False):
            return templates.TemplateResponse(
                request, "_docbuttons.html", {**ctx, "state": "generating"})
        try:
            if actions.application_state(out) == "ready":
                # Finished between the check above and acquiring the lock.
                return templates.TemplateResponse(
                    request, "_docbuttons.html", {**ctx, "state": "ready"})
            try:
                actions.run_generation(db_path, posting_id, out)
            except Exception as exc:                      # noqa: BLE001
                # Shown in place of the button. Never revert silently - that
                # reads as a dead click. Running out of credits mid-click is
                # a realistic case and must say so.
                return templates.TemplateResponse(
                    request, "_docbuttons.html",
                    {**ctx, "state": "error", "error": str(exc)})
            return templates.TemplateResponse(request, "_docbuttons.html",
                                              {**ctx, "state": "ready"})
        finally:
            lock.release()

    @app.get("/applications/{dirname}/{filename}")
    def document(dirname: str, filename: str):
        try:
            path = actions.safe_application_path(
                actions.APPLICATIONS_ROOT, dirname, filename)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not path.is_file():
            raise HTTPException(404, "not generated yet")
        return FileResponse(path)

    return app
