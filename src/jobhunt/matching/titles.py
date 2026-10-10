"""Boundary-safe title patterns, and the loose prefilter rule. Pure.

ONE definition of every title pattern, so the prefilter and any future caller
cannot grow divergent regexes.

Every pattern is word-boundary anchored. This project has shipped five
substring false positives, and one of them - `ux` matching "Linux" - is live
in the current data.
"""

from __future__ import annotations

import re

#: Anything suggesting a role they could plausibly do. Deliberately broad: this
#: is the SAFETY VALVE, so a false positive here only costs an API call while
#: a false negative discards a real opportunity.
POSITIVE_RE = re.compile(
    r"\b("
    r"design|designer|designs|ux|ui|user experience|interaction|"
    r"prototyp\w*|research|researcher|product|engineer|engineering|"
    r"developer|architect|technologist|creative|"
    # "Member of Technical Staff" is how the AI labs title an engineer.
    # 27 live rows carrying no `engineer` token at all - without this they
    # are invisible to classification AND unprotected by the override.
    r"technical staff"
    r")\b", re.I)

#: Unambiguous non-candidate functions. A term earns its place here only if no
#: plausible version of that role would suit them. When in doubt, leave it out -
#: the cost of omission is one API call.
_DENY = {
    "account executive": r"\baccount executives?\b",
    "sales": r"\b(sales (rep|representative|manager|director|lead)|sdr|bdr)\b",
    "revenue": r"\brevenue\b",
    "recruiter": (r"\b(recruiters?|recruiting (manager|lead)"
                  # A talent pool/community/network posting is an open-ended
                  # expression of interest, not a role.
                  r"|talent (acquisition|partner|coordinator|associate"
                  r"|pool|community|network|strategy))\b"),
    "accountant": r"\b(accountants?|bookkeepers?|controller|payroll)\b",
    "legal": r"\b(attorneys?|counsel|paralegals?)\b",
    "clinical": r"\b(nurses?|physicians?|clinicians?|therapists?)\b",
    "trades": r"\b(machinists?|welders?|assemblers?|electricians?|plumbers?)\b",
    "warehouse": r"\b(warehouse|forklift|drivers?|custodians?|janitors?)\b",
    "admin": r"\b(executive assistant|office manager|receptionist)\b",
    "finance ops": r"\b(auditors?|tax (manager|analyst)|procurement)\b",
    "support": r"\b(customer (support|success) (rep|representative|agent))\b",
    # --- added 2026-10-01, read off 659 unclassified live titles --------
    "technician": r"\btechnicians?\b",
    # Only the shift DESIGNATION forms. A bare `\bshift\b` would fire on
    # "Shift Left Security" - the sixth substring false positive, avoided.
    "shift work": (r"\b((first|second|third|fourth|1st|2nd|3rd|4th|night|weekend|day|swing|graveyard)\s+shift"
                   r"|shift\s+(lead|supervisor|manager|differential))\b"),
    # Abbreviation: case-SENSITIVE via a scoped inline flag, because the
    # whole dict is compiled with re.I. Slice 2 gotcha 1 - `\bUS\b` under
    # re.I matched the pronoun "us" in 39 of 260 posts.
    "go to market": r"\b(?-i:GTM)\b",
    "people ops": r"\b(head of people|people (operations|business partner))\b",
    "operations": r"\boperations (lead|manager)\b",
    "program manager": r"\bprogram managers?\b",
}
DENY_RE = {name: re.compile(p, re.I) for name, p in _DENY.items()}


def has_positive_signal(title: str | None) -> bool:
    return bool(POSITIVE_RE.search(title or ""))


def deny_reason(title: str | None) -> str | None:
    """The denylist family this title matches, ignoring the override."""
    for name, pattern in DENY_RE.items():
        if pattern.search(title or ""):
            return name
    return None


def prefilter_drop(title: str | None) -> str | None:
    """The reason to skip scoring this role, or None to score it.

    Loose by construction. A denylist hit is OVERRULED by any positive signal
    in the same title: "Senior Product Designer, Consumer Revenue" matches the
    denylist and must survive, because an earlier, tighter filter dropped
    exactly that title on the pattern `revenue`.

    The default is to KEEP. Failing toward spending an API call is the
    cheaper mistake.
    """
    if not title or not title.strip():
        return None
    if has_positive_signal(title):
        return None
    reason = deny_reason(title)
    return f"{reason}: no design, product or engineering signal" if reason else None
