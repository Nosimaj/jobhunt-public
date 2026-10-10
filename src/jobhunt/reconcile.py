from __future__ import annotations

import difflib
import re
from datetime import date

from .models import (Bullet, ChronologyGap, Gap, ResumeProfile, Identity,
                     IndependentEngagement, PdfRole, Profile, Provenance,
                     ReviewReport, Role, SourceRef)
from .normalize import AliasTable, normalize_company, slugify
from .overrides import Overrides

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# The export labels each contact URL with a parenthetical. The label is the only
# reliable signal: the URLs themselves are indistinguishable (two of the three
# are on the same domain), and their position is not fixed.
_LINK_LABELS = {"linkedin": "(LinkedIn)",
                "portfolio": "(Portfolio)",
                "personal": "(Personal)"}


def _links(header: list[str]) -> dict[str, str]:
    """Extract contact URLs from the PDF header by their parenthetical labels.

    The exporter puts the label either on its own line directly BELOW the URL
    (`www.linkedin.com/in/x` / `(LinkedIn)`) or inline after it
    (`example.com/ (Personal)`), so both forms are handled.

    Keying on the label rather than on the URL text matters: the previous
    implementation required "portfolio" to appear in the line, which the
    `(Personal)` URL never does, so `links.personal` -- a key spec §4 declares
    -- never populated at all. Keying on the label also removes the need for a
    positional window; the caller passes the whole header region, because a
    phone number added to the contact block shifts every line below it.
    """
    links: dict[str, str] = {}
    for idx, line in enumerate(header):
        for key, label in _LINK_LABELS.items():
            if label not in line or key in links:
                continue
            before = line[:line.index(label)].strip()
            url = before or (header[idx - 1].strip() if idx else "")
            if url:
                links[key] = url
    return links


def match_jobs(pdf_roles: list[PdfRole], html_jobs: list, alias: AliasTable
               ) -> tuple[dict[int, int], list[str]]:
    """Map pdf-role index -> html-job index by normalised employer, title as tiebreak.

    The mapping is enforced one-to-one: once a PDF index is claimed by one HTML
    job, a second HTML job that would also resolve to it is never allowed to
    overwrite the assignment. Such a collision is always reported as a
    conflict naming both HTML jobs and the contested PDF role -- never
    silently dropped, per the project's guard against unreported matches.
    """
    conflicts: list[str] = []
    by_company: dict[str, list[int]] = {}
    for i, role in enumerate(pdf_roles):
        key = normalize_company(alias.canonical(role.company))
        by_company.setdefault(key, []).append(i)

    mapping: dict[int, int] = {}
    for j, job in enumerate(html_jobs):
        key = normalize_company(alias.canonical(job.org))
        candidates = by_company.get(key, [])
        if not candidates:
            conflicts.append(
                f"HTML job '{job.org} / {job.title}' has no PDF counterpart; "
                f"its bullets cannot be attached to a dated role")
            continue

        if len(candidates) == 1:
            target = candidates[0]
        else:
            scored = sorted(
                candidates,
                key=lambda i: difflib.SequenceMatcher(
                    None, pdf_roles[i].title.casefold(), job.title.casefold()).ratio(),
                reverse=True)
            best, runner_up = scored[0], scored[1]
            best_score = difflib.SequenceMatcher(
                None, pdf_roles[best].title.casefold(), job.title.casefold()).ratio()
            runner_score = difflib.SequenceMatcher(
                None, pdf_roles[runner_up].title.casefold(), job.title.casefold()).ratio()
            if best_score - runner_score < 0.10:
                conflicts.append(
                    f"ambiguous match for HTML job '{job.org} / {job.title}': "
                    f"'{pdf_roles[best].title}' and '{pdf_roles[runner_up].title}' score "
                    f"{best_score:.2f}/{runner_score:.2f}; not guessed")
                continue
            target = best

        if target in mapping:
            other = html_jobs[mapping[target]]
            conflicts.append(
                f"HTML jobs '{other.org} / {other.title}' and '{job.org} / {job.title}' "
                f"both match PDF role '{pdf_roles[target].company} / "
                f"{pdf_roles[target].title}'; keeping the first match and reporting "
                f"the collision rather than overwriting it")
            continue

        mapping[target] = j
    return mapping, conflicts


def _identity(pdf_text_lines: list[str], html: ResumeProfile) -> Identity:
    """Identity comes from the PDF header block. Only structured fields are read.

    Name/headline/location are anchored to the literal "Summary" line rather
    than to fixed absolute offsets: the contact/skills preamble above it has
    no other reliable delimiter and can silently grow or shrink (e.g. a phone
    number added to the export), which would corrupt fixed-offset reads with
    no error. If pdf_text_lines is empty, or contains no "Summary" line, the
    export layout has changed in a way we cannot trust, so this raises loudly
    rather than guessing. It must never fall back to a blank identity: a
    Profile with an empty name is schema-valid and would ship as complete.
    """
    lines = pdf_text_lines
    if not lines:
        raise ValueError(
            "no PDF header lines supplied; identity cannot be derived and a "
            "blank identity must never be written to profile.json")
    email = next((m.group(0) for line in lines
                  if (m := _EMAIL_RE.search(line))), "")

    if "Summary" not in lines:
        raise ValueError(
            "PDF header block has no 'Summary' delimiter line; the export "
            "layout has changed and identity offsets cannot be trusted")

    summary_idx = lines.index("Summary")
    # The whole header region, not a fixed window: Ruling 14 removed absolute
    # offsets everywhere else for exactly this reason.
    links = _links(lines[:summary_idx])
    name = lines[summary_idx - 3] if summary_idx >= 3 else ""
    headline = lines[summary_idx - 2] if summary_idx >= 2 else ""
    location = lines[summary_idx - 1] if summary_idx >= 1 else ""
    return Identity(name=name, headline=headline, location=location,
                    email=email, links=links)


def _assign_role_ids(specs: list[tuple[str, str, str, str]]) -> list[str]:
    """Assign a stable id to each (company, start, end, title) spec.

    Ids are `slug(company)-YYYY-MM` from the *start month*, not the input
    position, so they never renumber if the PDF export re-orders roles. The
    only remaining source of collision is two roles at the same company
    starting in the same month; those are broken by sorting the colliding
    group deterministically on (start, end, title) -- never on input order --
    before assigning numeric suffixes.
    """
    bases = [f"{slugify(company)}-{start}" for company, start, _end, _title in specs]
    groups: dict[str, list[int]] = {}
    for idx, base in enumerate(bases):
        groups.setdefault(base, []).append(idx)

    ids: list[str | None] = [None] * len(specs)
    for base, idxs in groups.items():
        if len(idxs) == 1:
            ids[idxs[0]] = base
            continue
        ordered = sorted(idxs, key=lambda i: (specs[i][1], specs[i][2], specs[i][3]))
        for n, idx in enumerate(ordered):
            ids[idx] = base if n == 0 else f"{base}-{n + 1}"
    return ids  # type: ignore[return-value]


def _bullets_from_html(job, role_id: str, snapshot_date: str) -> list[Bullet]:
    return [Bullet(id=f"{role_id}-b{k}", text=b.text, audience=b.audience,
                   user_approved=True,
                   provenance=Provenance(source="resume", snapshot_date=snapshot_date))
            for k, b in enumerate(job.bullets, start=1)]


def _bullets_from_pdf(role: PdfRole, role_id: str, snapshot_date: str,
                      *, start: int = 1) -> list[Bullet]:
    # PDF prose is unreviewed: audience is always "untagged", never "both".
    # (Also enforced structurally by Bullet's model_validator.)
    #
    # `start` continues the numbering after any HTML bullets already assigned
    # to this role_id (see reconcile()), so a matched role's appended PDF
    # bullets never collide with its html-b{n} ids.
    return [Bullet(id=f"{role_id}-b{k}", text=text, audience="untagged",
                   user_approved=False,
                   provenance=Provenance(source="pdf", snapshot_date=snapshot_date))
            for k, text in enumerate(role.bullets, start=start)]


def _sort_key(role: Role) -> tuple[int, int, str, str]:
    """Reverse-chronological by start, then a total order on ties.

    Keying on `start` alone left Python's stable sort to fall back on PDF
    input order, so the two `contoso-2011-11` roles swapped places under
    most input permutations and `profile.json` was not byte-reproducible for
    identical input. `end` and the (unique) `id` make the order total.
    """
    year, month = (int(p) for p in role.start.split("-"))
    return (-year, -month, role.end, role.id)


def _month_index(ym: str) -> int:
    year, month = (int(p) for p in ym.split("-"))
    return year * 12 + month


def chronology_gaps(roles: list[Role], today: str) -> list[ChronologyGap]:
    """Gaps over two whole months between MERGED employment spans.

    Spans must be merged before gaps are measured. Several engagements run
    concurrently - the two 2011-2012 Initech Bank roles overlap each other and
    two Contoso roles - so comparing consecutive roles reports gaps that a
    parallel role already covers. Merging also makes the result independent of
    sort order.

    `today` supplies the horizon for a role whose end is "present", keeping the
    computation deterministic under test.

    Roles with `render == "omit"` are filtered out before spans are merged. A
    gap before the earliest role you actually show is not a gap, it is where
    your history starts.
    """
    roles = [r for r in roles if r.render != "omit"]
    if not roles:
        return []
    horizon = _month_index(today[:7])
    spans = sorted(
        ((_month_index(r.start),
          horizon if r.end == "present" else _month_index(r.end),
          r) for r in roles),
        key=lambda span: span[0])

    gaps: list[ChronologyGap] = []
    _, current_end, current_role = spans[0]
    for start, end, role in spans[1:]:
        if start - current_end > 2:
            gaps.append(ChronologyGap(after_role_id=current_role.id,
                                      before_role_id=role.id,
                                      months=start - current_end))
            current_end, current_role = end, role
        elif end > current_end:
            current_end, current_role = end, role
    return gaps


def reconcile(pdf_roles: list[PdfRole], html: ResumeProfile, overrides: Overrides,
              sources: list[SourceRef], *, pdf_header_lines: list[str],
              today: str | None = None,
              pdf_conflicts: list[str] | None = None,
              ) -> tuple[Profile, ReviewReport]:
    """Merge the two sources into one Profile plus a ReviewReport.

    `pdf_header_lines` is REQUIRED and keyword-only. It used to default to
    None, and omitting it produced a schema-valid Profile with a wholly blank
    `identity` and `complete: true` -- partial success dressed up as success.
    Every caller must now hand over `normalize_lines(pdf_text)` explicitly.
    """
    stamp = today or date.today().isoformat()
    snapshot_date = sources[0].snapshot_date if sources else stamp
    alias = AliasTable(overrides.aliases)
    report = ReviewReport(conflicts=list(pdf_conflicts or []))

    mapping, match_conflicts = match_jobs(pdf_roles, html.jobs, alias)
    report.conflicts.extend(match_conflicts)

    canonical_companies = [alias.canonical(r.company) for r in pdf_roles]
    pdf_specs = [(canonical_companies[i], pdf_roles[i].start, pdf_roles[i].end,
                  pdf_roles[i].title) for i in range(len(pdf_roles))]
    manual_specs = [(m.company, m.start, m.end, m.title_of_record)
                     for m in overrides.manual_roles]
    all_ids = _assign_role_ids(pdf_specs + manual_specs)
    pdf_ids = all_ids[:len(pdf_specs)]
    manual_ids = all_ids[len(pdf_specs):]

    roles: list[Role] = []
    for i, pdf_role in enumerate(pdf_roles):
        company = canonical_companies[i]
        role_id = pdf_ids[i]
        job = html.jobs[mapping[i]] if i in mapping else None

        if job is not None and job.title and job.title != pdf_role.title:
            report.conflicts.append(
                f"title differs for {company}: record '{pdf_role.title}' vs "
                f"presented '{job.title}' (both retained)")

        if job is not None:
            # Matched roles keep BOTH sources' bullets: the html prose is
            # reviewed and correctly voiced, so it leads; the pdf bullets are
            # real evidence too and are appended after it, rather than
            # discarded, with audience="untagged" (never reviewed) and
            # continuing the id numbering so they never collide with the
            # html bullets' ids.
            html_bullets = _bullets_from_html(job, role_id, snapshot_date)
            bullets = html_bullets + _bullets_from_pdf(
                pdf_role, role_id, snapshot_date, start=len(html_bullets) + 1)
        else:
            bullets = _bullets_from_pdf(pdf_role, role_id, snapshot_date)

        roles.append(Role(
            id=role_id,
            company=company,
            title_of_record=pdf_role.title,
            title_presented=(job.title if job and job.title else pdf_role.title),
            start=pdf_role.start,
            end=pdf_role.end,
            location=pdf_role.location,
            bullets=bullets,
        ))

    for k, manual in enumerate(overrides.manual_roles):
        role_id = manual_ids[k]
        roles.append(Role(
            id=role_id, company=manual.company,
            title_of_record=manual.title_of_record,
            title_presented=manual.title_presented,
            start=manual.start, end=manual.end, location=manual.location,
            render=manual.render, do_not_include=manual.do_not_include,
            talking_points=manual.talking_points,
            disclosure_rule=manual.disclosure_rule,
            bullets=[Bullet(id=f"{role_id}-b{n}", text=b.text, audience=b.audience,
                            user_approved=b.user_approved,
                            provenance=Provenance(source="manual",
                                                  snapshot_date=snapshot_date))
                     for n, b in enumerate(manual.bullets, start=1)],
        ))

    override_reasons: dict[str, str] = {}
    for ov in overrides.role_overrides:
        target_key = normalize_company(alias.canonical(ov.company))
        matches = [r for r in roles
                   if normalize_company(alias.canonical(r.company)) == target_key
                   and (ov.start is None or r.start == ov.start)]
        if not matches:
            detail = f"role_override for company '{ov.company}'"
            if ov.start:
                detail += f" starting {ov.start}"
            detail += " matched no role; check the company name (and start, if given) for a typo"
            report.conflicts.append(detail)
            continue
        for r in matches:
            r.render = ov.render
            if ov.reason:
                override_reasons[r.id] = ov.reason

    roles.sort(key=_sort_key)

    engagements = [
        IndependentEngagement(
            id=slugify(f"{e.client}-{e.start}"), client=e.client, work=e.work,
            start=e.start, end=e.end, paid=e.paid,
            dates_confirmed=e.dates_confirmed, supplement_only=True)
        for e in overrides.independent_engagements]

    gaps = chronology_gaps(roles, stamp)
    report.chronology_gaps = gaps

    report.render_directives = [
        f"{r.company} ({r.id}): render={r.render}"
        + (f" — disclosure_rule: {r.disclosure_rule}" if r.render == "omit"
           and r.disclosure_rule else "")
        + (f" — reason: {override_reasons[r.id]}" if r.id in override_reasons else "")
        for r in roles if r.render != "feature"]

    report.unapproved_bullets = [
        f"{r.company} ({r.id}): {b.text}"
        for r in roles for b in r.bullets if not b.user_approved
        and b.provenance.source == "manual"]

    report.unconfirmed_dates = [
        f"{e.client}: {e.start}..{e.end} unconfirmed"
        for e in engagements if not e.dates_confirmed]

    report.tagging_worklist = [
        f"{r.company} / {r.title_of_record} ({r.start})"
        for r in roles
        if r.bullets and all(b.audience == "untagged" for b in r.bullets)]

    # No filter on g.blocking: ReviewReport.blocking_gaps is blocking by
    # definition and enforces that itself. Filtering it here made the field
    # name and its contents disagree about what the field means.
    profile = Profile(
        generated_at=f"{stamp}T00:00:00", complete=not report.blocking_gaps,
        sources=sources,
        identity=_identity(pdf_header_lines, html),
        summaries=html.summaries, skills=html.skills, roles=roles,
        projects=html.projects,
        display_precision=overrides.display_precision,
        independent_engagements=engagements,
        chronology_gaps=gaps,
        gaps=[Gap(kind="chronology", detail=(
            f"{g.months}-month gap between {g.after_role_id} and "
            f"{g.before_role_id}"), blocking=False) for g in gaps])
    return profile, report
