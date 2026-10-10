"""The action matrix: role fit x company fit -> what to do about it.

Deliberately NOT in either prompt. Neither model decides the action; each
scores one axis and code combines them. A model that knows the action it is
steering towards can reason backwards into the axis that produces it, which is
precisely the leak the two-axis split exists to close.
"""

from __future__ import annotations

from typing import Literal

RoleFit = Literal["strong", "partial", "no"]
CompanyFit = Literal["target", "neutral", "no"]
Action = Literal[
    "apply_priority", "apply", "apply_if_comp", "apply_and_outreach",
    "maybe_queue", "outreach_lead", "drop",
]

#: Every (role_fit, company_fit) pair. Exhaustive by construction - a missing
#: pair would KeyError mid-run on a single posting, which is the worst place
#: to discover an incomplete table.
ACTION: dict[tuple[str, str], str] = {
    ("strong",  "target"):  "apply_priority",
    ("strong",  "neutral"): "apply",
    # Not dropped: a role this good is worth a look even at a company that
    # scored `no`, and burying it would hide a judgement the human should make.
    ("strong",  "no"):      "apply_if_comp",
    ("partial", "target"):  "apply_and_outreach",
    ("partial", "neutral"): "maybe_queue",
    ("partial", "no"):      "drop",
    # The cell the second axis was added for. Under one axis, "wrong role,
    # right company" had nowhere to go but `no`, which is how three of the
    # five eval disagreements on 2026-09-28 were generated.
    ("no",      "target"):  "outreach_lead",
    ("no",      "neutral"): "drop",
    ("no",      "no"):      "drop",
}


def derive_action(role_fit: str, company_fit: str) -> str:
    try:
        return ACTION[(role_fit, company_fit)]
    except KeyError:
        raise ValueError(
            f"no action for role_fit={role_fit!r} company_fit={company_fit!r}"
        ) from None
