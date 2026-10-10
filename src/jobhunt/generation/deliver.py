"""Edited markdown -> plain text, printable HTML, and form-filling JSON.

NO PDF LIBRARY. Every PDF option is a third-party dependency, and this project
has held "no new dependencies" through six slices. The HTML carries a print
stylesheet; The user prints to PDF from the browser, which also gives them a
last look before it becomes a file they send.
"""

from __future__ import annotations

import html as html_mod
import re

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_EMPH_RE = re.compile(r"[*_]{1,2}(.+?)[*_]{1,2}")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def _inline(text: str) -> str:
    text = _LINK_RE.sub(r"\1 (\2)", text)
    return _EMPH_RE.sub(r"\1", text)


def markdown_to_text(md: str) -> str:
    out = []
    for line in md.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            out.append(_inline(m.group(2)))
            continue
        out.append(_inline(line))
    # Collapse the runs of blank lines that removing syntax leaves behind.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip() + "\n"


_CSS = """
body { font: 11pt/1.45 Georgia, serif; max-width: 7.2in; margin: 0.5in auto;
       color: #111; }
h1 { font-size: 20pt; margin: 0 0 2pt; }
h2 { font-size: 12pt; text-transform: uppercase; letter-spacing: .06em;
     border-bottom: 1px solid #ccc; padding-bottom: 2pt; margin: 16pt 0 6pt; }
h3 { font-size: 11.5pt; margin: 10pt 0 0; }
ul { margin: 4pt 0 0 16pt; padding: 0; }
li { margin: 0 0 3pt; }
a { color: #111; }
@media print { body { margin: 0.5in; } a { text-decoration: none; } }
"""


def markdown_to_html(md: str, *, title: str) -> str:
    """Self-contained, printable HTML. No external CSS, no JavaScript - it has
    to survive being emailed or opened offline."""
    body, in_list = [], False
    for line in md.splitlines():
        bullet = _BULLET_RE.match(line)
        if bullet:
            if not in_list:
                body.append("<ul>"); in_list = True
            body.append(f"<li>{_inline(html_mod.escape(bullet.group(1)))}</li>")
            continue
        if in_list:
            body.append("</ul>"); in_list = False
        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            body.append(
                f"<h{level}>{_inline(html_mod.escape(heading.group(2)))}</h{level}>")
        elif line.strip():
            body.append(f"<p>{_inline(html_mod.escape(line))}</p>")
    if in_list:
        body.append("</ul>")
    return (f"<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            f"<title>{html_mod.escape(title)}</title>\n<style>{_CSS}</style>\n"
            f"</head>\n<body>\n" + "\n".join(body) + "\n</body>\n</html>\n")


#: Characters that cannot appear in a filename, plus the dashes they do not
#: want. The title becomes the PDF's suggested filename in Chrome, so a slash
#: here would create a directory and an em-dash trips some ATS upload forms.
#: A slash is REPLACED, not deleted - deleting turned the very plausible
#: "UX/UI Designer" into "UXUI Designer".
_TITLE_SEP_RE = re.compile(r"[/\\]")
_TITLE_STRIP_RE = re.compile(r"[:*?\"<>|\x00-\x1f]")

#: Every Unicode dash, not just the em-dash: hyphen (U+2010), non-breaking
#: hyphen (U+2011), figure/en/em/horizontal bar (U+2012-U+2015) and minus
#: (U+2212). They asked for plain hyphens ANYWHERE in a generated document.
_DASH_RE = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")

#: Placeholders that must never reach a filename a recruiter reads. "Role" is
#: the literal string `posting_markdown` emitted for years, because it read
#: `role_title` off a `Posting` - a model with no such field.
_PLACEHOLDERS = {"role", "untitled", "unknown", "n/a", "", "(no title)", "?",
                 "not stated", "none"}


def plain_dashes(text: str | None) -> str:
    """Every Unicode dash becomes a plain hyphen. Spacing is untouched.

    Applied where a document is BUILT rather than to the profile, because the
    em-dashes in their bullets are their own prose from their portfolio - normalising
    the rendered output is a presentation choice; editing `profile.json` would
    be rewriting what they wrote.
    """
    return _DASH_RE.sub("-", text or "")


def document_title(kind: str, name: str, company: str | None,
                   role: str | None) -> str:
    """`the candidate - Resume - Runway - Design Engineer`.

    Chrome's "Save as PDF" takes its suggested filename from the HTML
    `<title>`, so this string is what a recruiter sees attached to an
    application. Plain hyphens only, by request; an absent or placeholder
    segment is DROPPED rather than printed, because "- Role" in a filename
    reads worse than no segment at all.
    """
    parts = [name, kind, company, role]
    out = []
    for part in parts:
        part = _TITLE_SEP_RE.sub("-", _TITLE_STRIP_RE.sub("", part or ""))
        part = " ".join(plain_dashes(part).split()).strip(" -.")
        if part and part.casefold() not in _PLACEHOLDERS:
            out.append(part)
    return " - ".join(out)


def _field(md: str, label: str) -> str | None:
    m = re.search(rf"^- {re.escape(label)}:\s*(.+)$", md, re.M)
    return m.group(1).strip().strip("`") if m else None


def application_json(posting_md: str, resume_md: str) -> dict:
    """The form-filling payload, built from the EDITED resume.

    Shape is fixed by spec section 5 so it is not an implementer's invention
    that slice 5 then depends on.
    """
    # Explicit fields first. The H1 is a DISPLAY string, and parsing it meant
    # reader and writer had to agree on a separator - they silently stopped
    # agreeing when the writer's em-dash became a hyphen, and both company and
    # role vanished from the PDF filename. The regex stays only as a fallback
    # for the application directories written before these fields existed.
    company = _field(posting_md, "company")
    role = _field(posting_md, "role")
    title_line = re.search(r"^#\s+(.*?)\s+—\s+(.*)$", posting_md, re.M)
    roles, skills, current = [], [], None
    section = None
    for line in resume_md.splitlines():
        h2 = re.match(r"^##\s+(.*)$", line)
        if h2:
            section = h2.group(1).strip().casefold()
            continue
        h3 = re.match(r"^###\s+(.*?)\s+·\s+(.*)$", line)
        if h3:
            current = {"employer": h3.group(2).strip(),
                       "title_presented": h3.group(1).strip(),
                       "span": "", "bullets": []}
            roles.append(current)
            continue
        span = re.match(r"^\*(.+)\*$", line)
        if span and current and not current["span"]:
            current["span"] = span.group(1).strip()
            continue
        bullet = _BULLET_RE.match(line)
        if bullet and current:
            current["bullets"].append(_inline(bullet.group(1)).strip())
            continue
        if section == "skills" and line.strip():
            skills = [s.strip() for s in line.split(",") if s.strip()]
    return {
        "company": company or (title_line.group(2).strip() if title_line else None),
        "role_title": role or (title_line.group(1).strip() if title_line else None),
        "apply_url": _field(posting_md, "apply"),
        "location": _field(posting_md, "location"),
        "roles": roles,
        "skills": skills,
        "cited_case_studies": [
            {"title": t, "url": u}
            for t, u in _LINK_RE.findall(resume_md)],
    }
