"""Five provider JSON shapes into one `AtsRole`. Pure - no I/O.

Every gotcha here was found by probing the live APIs on 2026-09-28/29 and is
recorded in the spec. Do not re-derive them.
"""

from __future__ import annotations

import html
import re

from .models import AtsRole

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")


def strip_html(raw: str) -> str:
    """Unescape ONCE, strip tags, tidy whitespace.

    Exactly one unescape pass. Unescaping is not idempotent: a literal
    `&amp;lt;div&amp;gt;` in prose becomes `<div>` on a second pass and the
    tag stripper then deletes it. Greenhouse needs two passes and asks for
    them explicitly (see `_greenhouse`); nothing else may.
    """
    text = _TAG_RE.sub(" ", html.unescape(raw or ""))
    text = _WS_RE.sub("\n", text)
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def _month(value: str | None) -> str:
    return (value or "")[:10] or "unknown"


def _greenhouse(token, company, payload) -> list[AtsRole]:
    out = []
    for job in payload.get("jobs", []):
        loc = job.get("location") or {}
        # DOUBLE-escaped: content arrives as `&lt;p&gt;&lt;strong&gt;` -
        # escaped tags inside escaped text. One unescape leaves literal <p>
        # in the body for the model to read as content.
        body = strip_html(html.unescape(job.get("content") or ""))
        out.append(AtsRole(
            id=f"greenhouse:{token}:{job.get('id')}",
            provider="greenhouse", token=token, company=company,
            title=(job.get("title") or "").strip(),
            body=body,
            location=(loc.get("name") if isinstance(loc, dict) else str(loc)) or "",
            workplace_type="unknown",
            department=(job.get("departments") or [{}])[0].get("name", "") or "",
            apply_url=job.get("absolute_url"),
            posted_at=_month(job.get("updated_at") or job.get("first_published"))))
    return out


def _ashby(token, company, payload) -> list[AtsRole]:
    out = []
    for job in payload.get("jobs", []):
        workplace = (job.get("workplaceType") or "").casefold()
        if workplace not in ("remote", "onsite", "hybrid"):
            workplace = "remote" if job.get("isRemote") else "unknown"
        out.append(AtsRole(
            id=f"ashby:{token}:{job.get('id')}",
            provider="ashby", token=token, company=company,
            title=(job.get("title") or "").strip(),
            # descriptionPlain is already plain text - do NOT strip it again.
            body=(job.get("descriptionPlain") or "").strip(),
            location=job.get("location") or "",
            workplace_type=workplace,
            department=job.get("department") or job.get("team") or "",
            apply_url=job.get("applyUrl") or job.get("jobUrl"),
            posted_at=_month(job.get("publishedAt"))))
    return out


def _lever_lists(lists) -> str:
    """Lever's duties live in `lists`, NOT in descriptionPlain.

    Measured on findigs: descriptionPlain 2,605 chars, lists 4,227. The
    responsibilities and requirements - exactly what the Hybrid Test reads -
    are here. `lists` is a list of {"text": heading, "content": html}, so
    str() on it yields a Python repr, not prose.
    """
    parts = []
    for block in lists or []:
        if not isinstance(block, dict):
            continue
        heading = strip_html(block.get("text") or "")
        content = strip_html(block.get("content") or "")
        parts.append(f"{heading}\n{content}".strip())
    return "\n\n".join(p for p in parts if p)


def _lever(token, company, payload) -> list[AtsRole]:
    out = []
    for job in payload if isinstance(payload, list) else []:
        cats = job.get("categories") or {}
        body = "\n\n".join(p for p in (
            (job.get("descriptionPlain") or "").strip(),
            _lever_lists(job.get("lists")),
            (job.get("additionalPlain") or "").strip()) if p)
        workplace = (job.get("workplaceType") or "").casefold()
        out.append(AtsRole(
            id=f"lever:{token}:{job.get('id')}",
            provider="lever", token=token, company=company,
            title=(job.get("text") or "").strip(),
            body=body,
            location=cats.get("location") or "",
            workplace_type=workplace if workplace in
                ("remote", "onsite", "hybrid") else "unknown",
            department=cats.get("department") or "",
            apply_url=job.get("hostedUrl") or job.get("applyUrl"),
            posted_at=_month(job.get("createdAt") and
                             __import__("datetime").datetime.fromtimestamp(
                                 job["createdAt"] / 1000).isoformat())))
    return out


def _workable(token, company, payload) -> list[AtsRole]:
    out = []
    for job in payload.get("jobs", []):
        place = ", ".join(str(job[k]).strip() for k in ("city", "state", "country")
                          if job.get(k) and str(job[k]).strip())
        out.append(AtsRole(
            id=f"workable:{token}:{job.get('shortcode')}",
            provider="workable", token=token, company=company,
            title=(job.get("title") or "").strip(),
            # Single-escaped HTML: one unescape pass, via strip_html only.
            body=strip_html(job.get("description") or ""),
            location=place,
            # The only provider with a real boolean remote flag.
            workplace_type="remote" if job.get("telecommuting") is True else "unknown",
            department=job.get("department") or "",
            apply_url=job.get("application_url") or job.get("url"),
            posted_at=_month(job.get("published_on"))))
    return out


def _teamtailor_location(posting) -> str:
    locs = posting.get("jobLocation") if isinstance(posting, dict) else None
    if not isinstance(locs, list) or not locs or not isinstance(locs[0], dict):
        return ""
    addr = locs[0].get("address")
    if not isinstance(addr, dict):
        return ""
    parts = [str(addr[k]).strip() for k in
             ("addressLocality", "addressRegion", "addressCountry")
             if addr.get(k) and str(addr[k]).strip()]
    if not parts and addr.get("streetAddress"):
        parts = [str(addr["streetAddress"]).strip()]
    return ", ".join(parts)


def _teamtailor(token, company, payload) -> list[AtsRole]:
    out = []
    for item in payload.get("items", []):
        posting = item.get("_jobposting")
        out.append(AtsRole(
            id=f"teamtailor:{token}:{item.get('id')}",
            provider="teamtailor", token=token, company=company,
            title=(item.get("title") or "").strip(),
            body=strip_html(item.get("content_html") or ""),
            location=_teamtailor_location(posting),
            workplace_type="unknown",
            department="",
            apply_url=item.get("url"),
            posted_at=_month(item.get("date_published"))))
    return out


_PARSERS = {"greenhouse": _greenhouse, "ashby": _ashby, "lever": _lever,
            "workable": _workable, "teamtailor": _teamtailor}


def parse(provider: str, token: str, company: str, payload) -> list[AtsRole]:
    if provider not in _PARSERS:
        raise ValueError(f"unknown provider {provider!r}")
    return _PARSERS[provider](token, company, payload)
