from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from .models import PdfRole

_MONTHS = ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"]
_MONTH_NUM = {name: i + 1 for i, name in enumerate(_MONTHS)}
_MONTH_ALT = "|".join(_MONTHS)

# NB: the real export separates these tokens with U+00A0, normalised before matching.
DATE_RE = re.compile(
    rf"^({_MONTH_ALT}) (\d{{4}}) - (Present|(?:{_MONTH_ALT}) \d{{4}}) \(([^)]+)\)$")
_END_RE = re.compile(rf"^({_MONTH_ALT}) (\d{{4}})$")
DURATION_RE = re.compile(r"^\d+ (?:year|month)s?(?: \d+ months?)?$")
FOOTER_RE = re.compile(r"^Page \d+ of \d+$")
_BULLET_MARKERS = ("•", "▫")   # BULLET, WHITE SMALL SQUARE


def normalize_lines(text: str) -> list[str]:
    """Strip non-breaking spaces, page footers and blank lines."""
    text = text.replace("\xa0", " ")   # U+00A0 -> space; see note 1 above
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or FOOTER_RE.match(line):
            continue
        lines.append(line)
    return lines


def month_to_iso(month: str, year: str) -> str:
    return f"{int(year):04d}-{_MONTH_NUM[month]:02d}"


def duration_months(text: str) -> int | None:
    years = re.search(r"(\d+) year", text)
    months = re.search(r"(\d+) month", text)
    if not years and not months:
        return None
    return (int(years.group(1)) * 12 if years else 0) + (int(months.group(1)) if months else 0)


def span_months(start: str, end: str) -> int:
    """Inclusive month count, matching LinkedIn's own arithmetic."""
    y1, m1 = (int(p) for p in start.split("-"))
    y2, m2 = (int(p) for p in end.split("-"))
    return (y2 - y1) * 12 + (m2 - m1) + 1


def _is_prose(line: str) -> bool:
    """True when a line is body text rather than a company or title line."""
    return line.startswith(_BULLET_MARKERS) or line.endswith(".") or len(line) > 60


def _is_location(line: str) -> bool:
    return (len(line) <= 40 and not line.startswith(_BULLET_MARKERS)
            and not line.endswith(".") and not DATE_RE.match(line))


def _split_bullets(block: list[str]) -> list[str]:
    """Reassemble wrapped bullet text.

    Marked blocks start a new bullet at each marker. Unmarked blocks (older
    roles carry no marker at all) start a new bullet after a line that ends a
    sentence.

    In a marked block, any line appearing BEFORE the first marker belongs to
    no marked bullet. Four engagements in the real export open with unmarked
    ``Project:`` / ``Desc:`` lines that are the ONLY description those roles
    have. These used to fall through the continuation branch while the bullet
    list was still empty and were discarded with no conflict, so eight lines
    of real content vanished. Each such leading line is now emitted as its own
    bullet, ahead of the first marked bullet: nothing is silently dropped.
    """
    if not block:
        return []
    if any(line.startswith(_BULLET_MARKERS) for line in block):
        first_marker = next(i for i, line in enumerate(block)
                            if line.startswith(_BULLET_MARKERS))
        bullets: list[list[str]] = [[line] for line in block[:first_marker]]
        for line in block[first_marker:]:
            if line.startswith(_BULLET_MARKERS):
                bullets.append([line.lstrip("".join(_BULLET_MARKERS)).strip()])
            else:
                bullets[-1].append(line)
        return [" ".join(parts).strip() for parts in bullets]

    bullets, current = [], []
    for line in block:
        current.append(line)
        if line.endswith((".", "!", "?")):
            bullets.append(" ".join(current).strip())
            current = []
    if current:
        bullets.append(" ".join(current).strip())
    return bullets


def _resolve_headers(lines: list[str], date_idx: list[int]) -> tuple[list[str], list[int]]:
    """First pass: resolve each role's company and the start index of its header block.

    A role's header is the line(s) directly above its title that identify the
    employer: either a plain company line one line up, or -- for roles under a
    "group" employer (a company line followed by a bare duration line, e.g.
    ``Contoso`` / ``1 year 4 months``) -- the company line *two* lines above
    the title, with a following role in the same group inheriting the company
    and having no header of its own (its header_start is just its own title
    index, since nothing separates it from the previous role's last bullet).

    ``header_start[n]`` doubles as the exclusive upper bound for role n-1's
    body: the previous role's bullets must stop before the next role's company
    / group-header lines begin, or the next employer's name leaks into the
    last bullet.
    """
    companies: list[str] = []
    header_start: list[int] = []
    active_company: str | None = None

    for i in date_idx:
        title_idx = i - 1
        above_idx = i - 2
        above = lines[above_idx] if above_idx >= 0 else ""

        if DURATION_RE.match(above):
            active_company = lines[i - 3]
            start = i - 3
        elif above and not _is_prose(above) and not DATE_RE.match(above):
            active_company = above
            start = above_idx
        else:
            start = title_idx  # continuation of the still-active group/company

        if active_company is None:
            raise ValueError(
                f"role '{lines[title_idx]}' has no resolvable employer; "
                "the export layout has changed")

        companies.append(active_company)
        header_start.append(start)

    return companies, header_start


def parse_text(text: str) -> tuple[list[PdfRole], list[str]]:
    """Segment the flattened export into roles. Returns (roles, conflicts)."""
    lines = normalize_lines(text)
    date_idx = [i for i, line in enumerate(lines) if DATE_RE.match(line)]
    if not date_idx:
        raise ValueError(
            "no role date-ranges found in PDF text; the export layout has changed "
            "(check that non-breaking spaces are being normalised)")

    companies, header_start = _resolve_headers(lines, date_idx)

    roles: list[PdfRole] = []
    conflicts: list[str] = []

    for n, i in enumerate(date_idx):
        title = lines[i - 1]
        company = companies[n]

        month, year, end_raw, duration_text = DATE_RE.match(lines[i]).groups()
        start = month_to_iso(month, year)
        if end_raw == "Present":
            end = "present"
        else:
            em, ey = _END_RE.match(end_raw).groups()
            end = month_to_iso(em, ey)

        # Body runs from just after the date line up to (not including) the
        # start of the *next* role's header -- which is exactly where the
        # next role's company/group-header lines begin, so they never leak
        # into this role's trailing bullet.
        body_end = header_start[n + 1] if n + 1 < len(date_idx) else len(lines)
        body = lines[i + 1:body_end]
        location = None
        if body and _is_location(body[0]):
            location, body = body[0], body[1:]

        if end != "present":
            claimed = duration_months(duration_text)
            actual = span_months(start, end)
            if claimed is not None and claimed != actual:
                conflicts.append(
                    f"{company} / {title}: stated duration '{duration_text}' "
                    f"({claimed} months) disagrees with {start}..{end} ({actual} months)")

        roles.append(PdfRole(company=company, title=title, start=start, end=end,
                             duration_text=duration_text, location=location,
                             bullets=_split_bullets(body)))
    return roles, conflicts


def extract_text(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"LinkedIn PDF source not found: {path}")
    reader = PdfReader(path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_file(path: Path) -> tuple[list[PdfRole], list[str]]:
    return parse_text(extract_text(path))
