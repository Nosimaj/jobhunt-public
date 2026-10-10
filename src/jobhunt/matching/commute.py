"""Is this role's location within commuting distance?

A RANKING signal. Never a gate, and never rendered into a rubric - see the
module docstring of `tests/test_commute.py` for why.
"""

from __future__ import annotations

import re

#: USPS state abbreviation -> full name. A configured metro like
#: "Cambridge, MA" must match BOTH "Cambridge, MA" and "Cambridge,
#: Massachusetts" - measured against all 1,102 live ATS board locations on
#: 2026-09-29, both forms are common ("Bethesda, Maryland" (41) alongside
#: "Andover, MA" (30), "Bethesda, MD" (20)). `\bMA\b` cannot match inside
#: "Massachusetts" because the word boundary fails mid-word, so the state is
#: matched as EITHER form rather than as a literal substring of the metro.
_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico",
}


def _city_and_state(metro: str) -> tuple[str, str | None]:
    """Split "Cambridge, MA" into ("Cambridge", "MA"); "Boston" into
    ("Boston", None). A metro with no configured state matches on city
    alone - true today of "Boston", "Waltham", "Providence", "Nashua"."""
    city, _, state = metro.partition(",")
    state = state.strip()
    return city.strip(), (state or None)


def is_commutable(location: str, metros: list[str]) -> bool:
    text = (location or "").casefold()
    if not text.strip():
        return False
    for metro in metros:
        city, state = _city_and_state(metro)
        # Word-boundary anchored: "Newton, MA" must not match "Newtonville",
        # and this project has now produced four substring false positives
        # (`\bUS\b`/"us", perks text/"health", `ux`/"Linux", `revenue`).
        if not re.search(rf"\b{re.escape(city.casefold())}\b", text):
            continue
        if state is None:
            return True
        # The state must still be checked - dropping it once the city
        # matches would let "Cambridge, UK" match "Cambridge, MA". Accept
        # either the configured form or its expansion/abbreviation, never a
        # bare substring: "MA" must not match inside "Massachusetts", and
        # the full name gives the check a place to hold instead.
        forms = {state.casefold()}
        full = _STATE_NAMES.get(state.upper())
        if full:
            forms.add(full.casefold())
        if any(re.search(rf"\b{re.escape(form)}\b", text) for form in forms):
            return True
    return False
