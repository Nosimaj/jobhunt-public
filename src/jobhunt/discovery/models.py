from __future__ import annotations

from typing import Literal

from ..models import Strict

Source = Literal["hn", "greenhouse", "ashby", "lever", "workable",
                "teamtailor"]
WorkplaceType = Literal["remote", "onsite", "hybrid", "unknown"]
GeoSignal = Literal["us", "eu_only", "unknown"]
RejectReason = Literal["not_remote", "eu_only", "no_role_signal", "parse_error"]


class HnThreadRef(Strict):
    id: str
    title: str
    created_at: str          # ISO-8601 from Algolia


class HnRawPost(Strict):
    item_id: str
    text: str
    created_at: str | None = None


class ParsedPost(Strict):
    """The dependable fields only. Role, salary and location are deliberately
    absent - their order in the source is inconsistent (spec section 3.3)."""
    id: str                  # "hn:<item_id>"
    company: str
    apply_url: str | None
    is_remote: bool
    geo_signal: GeoSignal
    raw_text: str


class Posting(Strict):
    id: str
    source: Source
    source_thread_id: str
    company: str
    company_normalised: str
    raw_text: str
    apply_url: str | None
    is_remote: bool
    geo_signal: GeoSignal
    posted_at: str           # YYYY-MM-01, the thread's month
    first_seen: str
    last_seen: str
    # Deliberately a plain str, not a Literal: slice 2 writes only "new" and
    # "rejected", but slices 3-5 extend the vocabulary and must not be blocked
    # by this model.
    status: str
    reject_reason: RejectReason | None = None
    # v5. Set when the company's board stops listing the role - a fact an
    # immutable HN thread cannot supply, so it stays None for `hn` rows.
    # Optional with a None default: `Strict` forbids extras but every
    # existing construction predates this field.
    closed_at: str | None = None


class AtsRole(Strict):
    """One role from one company's job board, provider-normalised."""
    id: str                  # "<provider>:<token>:<job_id>"
    provider: str
    token: str
    company: str
    title: str
    body: str                # duties and requirements, plain text
    location: str            # verbatim; "" when the board gave none
    workplace_type: WorkplaceType
    department: str
    apply_url: str | None
    posted_at: str           # YYYY-MM-DD


class DiscoveryRun(Strict):
    id: int | None = None
    started_at: str
    finished_at: str | None = None
    thread_id: str | None = None
    posts_seen: int = 0
    candidates: int = 0
    rejected: int = 0
    new_postings: int = 0
    error: str | None = None
