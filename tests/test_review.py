# tests/test_review.py
from jobhunt.models import ChronologyGap, Gap, ReviewReport
from jobhunt.review import render_review


def test_sections_appear_in_spec_order():
    md = render_review(ReviewReport(), "2026-09-19T00:00:00", complete=True)
    order = [md.index(h) for h in ("## 1. Blocking gaps", "## 2. Chronology gaps",
                                   "## 3. Conflicts", "## 4. Tagging worklist",
                                   "## 5. Render directives",
                                   "## 6. Unapproved bullets",
                                   "## 7. Unconfirmed engagement dates")]
    assert order == sorted(order)


def test_empty_sections_say_none_rather_than_being_omitted():
    md = render_review(ReviewReport(), "2026-09-19T00:00:00", complete=True)
    assert md.count("_None._") == 7


def test_complete_flag_is_stated_at_the_top():
    md = render_review(ReviewReport(), "2026-09-19T00:00:00", complete=True)
    assert "**complete:** true" in md
    blocked = render_review(
        ReviewReport(blocking_gaps=[Gap(kind="x", detail="d", blocking=True)]),
        "2026-09-19T00:00:00", complete=False)
    assert "**complete:** false" in blocked


def test_content_is_rendered_as_list_items():
    report = ReviewReport(
        chronology_gaps=[ChronologyGap(after_role_id="a", before_role_id="b", months=13)],
        conflicts=["title differs for Tidewater"],
        tagging_worklist=["Contoso / UX/UI Design Developer (2013-11)"],
        unapproved_bullets=["Acme (acme-2020): draft text"],
        unconfirmed_dates=["Acme Co: 2022-03..2022-05 unconfirmed"],
        render_directives=["Acme (acme-2020): render=omit"])
    md = render_review(report, "2026-09-19T00:00:00", complete=True)
    assert "- 13-month gap between `a` and `b`" in md
    assert "- title differs for Tidewater" in md
    assert "- Contoso / UX/UI Design Developer (2013-11)" in md
    assert "- Acme (acme-2020): render=omit" in md
    assert md.count("_None._") == 1          # only blocking gaps is empty
