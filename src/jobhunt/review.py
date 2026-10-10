from __future__ import annotations

from .models import ReviewReport

_NONE = "_None._"


def _section(title: str, items: list[str]) -> str:
    body = "\n".join(f"- {line}" for line in items) if items else _NONE
    return f"## {title}\n\n{body}\n"


def render_review(report: ReviewReport, generated_at: str, complete: bool) -> str:
    """Render the worklist. Order is fixed by spec section 5: blocking first."""
    gap_lines = [f"{g.months}-month gap between `{g.after_role_id}` and "
                 f"`{g.before_role_id}`" for g in report.chronology_gaps]
    blocking_lines = [f"**{g.kind}** — {g.detail}" for g in report.blocking_gaps]

    parts = [
        "# Profile Ingestion Review",
        "",
        f"**generated:** {generated_at}  ",
        f"**complete:** {'true' if complete else 'false'}",
        "",
        "Regenerated on every run. Sections are ordered blocking-first.",
        "",
        _section("1. Blocking gaps", blocking_lines),
        _section("2. Chronology gaps", gap_lines),
        _section("3. Conflicts", report.conflicts),
        _section("4. Tagging worklist", report.tagging_worklist),
        _section("5. Render directives", report.render_directives),
        _section("6. Unapproved bullets", report.unapproved_bullets),
        _section("7. Unconfirmed engagement dates", report.unconfirmed_dates),
    ]
    return "\n".join(parts)
