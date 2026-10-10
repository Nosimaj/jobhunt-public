"""Fixtures shared across the dashboard test modules.

`seeded` lives here, not in `test_dashboard_app.py`, because
`test_dashboard_actions.py` needs it too. Importing it across test *modules*
(`from tests.test_dashboard_app import seeded`) required adding the repo root
to pytest's `pythonpath`, which let `tests.test_dashboard_app` (an implicit
namespace-package import) and `test_dashboard_app` (pytest's own rootdir
import of the same file) coexist as two distinct module objects - fixed by
giving both test modules one shared, un-ambiguous home instead.
"""

from pathlib import Path

from jobhunt import config
from jobhunt.discovery.models import Posting

# A fresh clone has no data/profile.json until `ingest` runs. Fall back to the
# anonymised golden profile so the prompt and CLI suites run instead of
# skipping.
if not config.PROFILE_PATH.is_file():
    config.PROFILE_PATH = Path(__file__).parent / "fixtures" / "profile_golden.json"
from jobhunt.discovery.store import Store
from jobhunt.matching.models import CompanyVerdict, RoleVerdict


def seeded(tmp_path):
    db = tmp_path / "j.sqlite"
    s = Store(db)
    s.init_schema()
    p = Posting(id="ashby:runway:1", source="ashby",
                source_thread_id="runway", company="Runway",
                company_normalised="runway",
                raw_text="Design Engineer. " + "duties " * 60,
                apply_url="https://apply/here", is_remote=True,
                geo_signal="us", posted_at="2026-09-01", first_seen="a",
                last_seen="a", status="new")
    s.upsert_posting(p)
    s.save_role_verdict(p.id, RoleVerdict(
        role_title="Design Engineer", discipline="design_engineering",
        reason="Spans design and engineering.", hybrid_test="pass",
        domain_fit="none",
        hybrid_evidence=["usability testing"], overlaps=["design systems"],
        gaps=[], role_fit="strong", location="Remote"),
        model="m", prompt_sha="s")
    s.save_company_verdict("runway", "Runway", CompanyVerdict(
        reason="Builds a real product.", domain_pull="present",
        designs_how_things_work="present", design_valued="present",
        stage_fit="unknown", reachability="unknown", disqualifier=None,
        company_fit="target"), model="m", prompt_sha="s")
    s.close()
    return db
