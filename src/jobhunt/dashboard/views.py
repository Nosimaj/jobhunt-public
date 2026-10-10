"""Rows to view models. PURE - no FastAPI, no sqlite3, no filesystem.

All the interesting logic lives here so it is tested without a server: the
derived fit score, the outreach-by-company roll-up, and the ordering.
"""

from __future__ import annotations

from pydantic import Field

from ..matching.commute import is_commutable
from ..matching.fit import derive_action
from ..models import Strict

MAX_SCORE = 12

#: Domain relevance, from rubric A's `domain_fit`. A NULL (a row scored before
#: the column existed) is 0 - not a free pass.
_DOMAIN_POINTS = {"direct": 2, "adjacent": 1, "none": 0}

#: `n/a` on the hybrid test means the gate does not apply. For a design role
#: that is ideal; for a sales role it means nothing. Scoring it unconditionally
#: put a growth-manager posting at 5 of 10 under the old denominator (spec section 10).
_DESIGN_DISCIPLINES = frozenset({"design", "design_engineering"})

#: Buckets in the order Sam should work through them. An apply_priority at
#: 6 is a better next action than an outreach_lead at 8, so the bucket is the
#: primary structure and the score orders within it.
BUCKET_ORDER = ("apply_priority", "apply_and_outreach", "apply",
                "apply_if_comp", "maybe_queue", "outreach_lead", "drop")

#: Rendered as company roll-ups rather than one row per role.
GROUPED = frozenset({"outreach_lead"})


def location_point(role: dict, location: str, metros: list[str]) -> int:
    """Prefer the audited `reach`; fall back to the commute check when NULL."""
    reach = role.get("reach")
    if reach is not None:
        return 1 if reach in ("remote", "local") else 0
    return 1 if ("remote" in location.casefold()
                 or is_commutable(location, metros)) else 0


def fit_points(role: dict, company_fit: str | None,
               metros: list[str]) -> dict[str, int]:
    """The six dimensions, before the cap."""
    hybrid = 0
    ht = role.get("hybrid_test")
    if ht == "pass":
        hybrid = 2
    elif ht == "n/a" and role.get("role_discipline") in _DESIGN_DISCIPLINES:
        hybrid = 2
    elif ht == "weak":
        hybrid = 1
    location = role.get("location") or ""
    return {
        "role": {"strong": 4, "partial": 2}.get(role.get("role_fit"), 0),
        "hybrid": hybrid,
        "company": {"target": 2, "neutral": 1}.get(company_fit, 0),
        "evidence": 1 if len(role.get("role_overlaps") or []) >
                         len(role.get("role_gaps") or []) else 0,
        "location": location_point(role, location, metros),
        "domain": _DOMAIN_POINTS.get(role.get("domain_fit") or "none", 0),
    }


def fit_score(role: dict, company_fit: str | None,
              metros: list[str]) -> int:
    """The composite out of MAX_SCORE.

    A `role_fit` of `no` caps the total at 3: the role is the thing they would
    actually do, so a definite no caps the score however good the company is.
    Without the cap, 'Join our Talent Community!' scored 5 of 10 under the old denominator.
    """
    total = sum(fit_points(role, company_fit, metros).values())
    return min(total, 3) if role.get("role_fit") == "no" else total


class RoleView(Strict):
    id: str
    company: str
    role_title: str | None = None
    location: str | None = None
    reach: str | None = None
    apply_url: str | None = None
    reason: str | None = None
    score: int
    points: dict[str, int]
    role_fit: str | None = None
    hybrid_test: str | None = None
    hybrid_evidence: list[str] = Field(default_factory=list)
    overlaps: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    seniority_note: str | None = None
    company_fit: str | None = None
    app_dirname: str = ""
    app_state: str = "absent"


class CompanyGroup(Strict):
    company: str
    company_fit: str | None = None
    role_count: int
    best_score: int


class Bucket(Strict):
    action: str
    count: int
    roles: list[RoleView] = Field(default_factory=list)
    companies: list[CompanyGroup] = Field(default_factory=list)


class Page(Strict):
    buckets: list[Bucket]
    scored: int
    not_yet_scored: int
    prefiltered: int = 0


def _default_app_state_for(company: str, role_title: str | None) -> tuple[str, str]:
    return "", "absent"


def _to_view(r: dict, metros: list[str], app_state_for) -> RoleView:
    cf = r.get("company_fit")
    pts = fit_points(r, cf, metros)
    dirname, state = app_state_for(r["company"], r.get("role_title"))
    return RoleView(
        id=r["id"], company=r["company"], role_title=r.get("role_title"),
        location=r.get("location"), reach=r.get("reach"),
        apply_url=r.get("apply_url"),
        reason=r.get("role_reason"), score=fit_score(r, cf, metros),
        points=pts, role_fit=r.get("role_fit"),
        hybrid_test=r.get("hybrid_test"),
        hybrid_evidence=list(r.get("hybrid_evidence") or []),
        overlaps=list(r.get("role_overlaps") or []),
        gaps=list(r.get("role_gaps") or []),
        seniority_note=r.get("seniority_note"), company_fit=cf,
        app_dirname=dirname, app_state=state)


def build_page(rows: list[dict], metros: list[str], prefiltered: int = 0,
              app_state_for=None) -> Page:
    """Group, score and order.

    The action is DERIVED here rather than read from `postings.action`, because
    that column is only written by `refresh_actions()` at the END of a matching
    run. Deriving is what makes the page fill in live while scoring proceeds.

    `app_state_for` stays optional and pure-default so this module never
    imports `actions` (filesystem access) - `app.py` injects the real
    callable; tests and callers that don't care about generation state get
    "absent" for everything.
    """
    app_state_for = app_state_for or _default_app_state_for
    by_action: dict[str, list[RoleView]] = {a: [] for a in BUCKET_ORDER}
    not_scored = 0
    for r in rows:
        # Out-of-area roles are excluded before scoring, so they should never
        # arrive; skip them here too. NULL reach is "not yet audited" and
        # stays visible.
        if r.get("reach") == "out_of_area":
            continue
        if not r.get("role_fit") or not r.get("company_fit"):
            not_scored += 1
            continue
        action = derive_action(r["role_fit"], r["company_fit"])
        by_action.setdefault(action, []).append(
            _to_view(r, metros, app_state_for))

    buckets = []
    for action in BUCKET_ORDER:
        views = sorted(by_action.get(action, []), key=lambda v: -v.score)
        if action in GROUPED:
            groups: dict[str, CompanyGroup] = {}
            for v in views:
                g = groups.get(v.company)
                if g is None:
                    groups[v.company] = CompanyGroup(
                        company=v.company, company_fit=v.company_fit,
                        role_count=1, best_score=v.score)
                else:
                    groups[v.company] = g.model_copy(update={
                        "role_count": g.role_count + 1,
                        "best_score": max(g.best_score, v.score)})
            ordered = sorted(groups.values(), key=lambda g: -g.best_score)
            buckets.append(Bucket(action=action, count=len(views),
                                  companies=ordered))
        else:
            buckets.append(Bucket(action=action, count=len(views), roles=views))
    return Page(buckets=buckets,
                scored=sum(len(v) for v in by_action.values()),
                not_yet_scored=not_scored, prefiltered=prefiltered)
