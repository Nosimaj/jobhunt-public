"""Which ATS roles are worth storing. GEOGRAPHY ONLY.

There is deliberately no discipline filter here. One was measured on
2026-09-28 and rejected: excluding obvious non-candidates removed 157 of 1,039
roles, saved $4.39, and wrongly dropped "Senior Product Designer, Consumer
Revenue" because the pattern contained `revenue`. Fifteen percent is not worth
losing a real design role, and it was the fourth keyword-filter false positive
found in this project. Discipline is rubric A's job.
"""

from __future__ import annotations

import re
import unicodedata

from .models import AtsRole

#: Case-SENSITIVE for the abbreviations, following the slice 2 lesson: `\bUS\b`
#: with re.I matches the pronoun "us" and mislabelled 39 of 260 posts.
#:
#: The state list is an explicit allowlist, not a bare `[A-Z]{2}`. Measured
#: against 1,102 real locations from 42 live boards: a bare two-letter class
#: matches any foreign ISO-2 code too (e.g. "Budapest, HU"). Excluding known
#: foreign codes instead of allowlisting US ones was tried and rejected -
#: state/country codes collide (CO=Colorado/Colombia, ID=Idaho/Indonesia,
#: CA=California/Canada, IN=Indiana/India, DE=Delaware/Germany,
#: MD=Maryland/Moldova) and an exclusion list would break real strings like
#: "Golden, CO", "Idaho Falls, ID", "Santa Clara, CA", "Bethesda, MD".
_US_STATE_CODES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    "MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|"
    "WA|WV|WI|WY|DC|PR"
)
_US_ABBREV_RE = re.compile(rf"\b(US|USA|U\.S\.|{_US_STATE_CODES})\b")

#: Full US state names, as words (so `re.I` is appropriate, unlike the
#: abbreviations above). **"Georgia" is deliberately excluded** - it is both
#: a US state and a country, and adding it here would read "Tbilisi,
#: Georgia" as a US location. `unknown` is the conservative, deliberate
#: answer for the bare token; see the ats_filters tests for the reasoning.
#: District of Columbia and Puerto Rico are included alongside the 50
#: states, per the measured gap (Bethesda, Maryland and friends).
_US_STATE_NAMES = (
    r"alabama|alaska|arizona|arkansas|california|colorado|connecticut|"
    r"delaware|florida|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|"
    r"louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|"
    r"missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|"
    r"new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|"
    r"rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|"
    r"virginia|washington|west virginia|wisconsin|wyoming|"
    r"district of columbia|puerto rico"
)
_US_WORDS_RE = re.compile(
    r"united states|remote|anywhere|"
    r"new york|san francisco|boston|cambridge|seattle|austin|chicago|denver|"
    r"los angeles|atlanta|miami|portland|philadelphia|washington|"
    r"bay area|nyc|brooklyn|oakland|" + _US_STATE_NAMES, re.I)
_NON_US_RE = re.compile(
    r"london|manchester|berlin|munich|stockholm|amsterdam|paris|dublin|madrid|"
    r"barcelona|lisbon|warsaw|zurich|geneva|bengaluru|bangalore|mumbai|delhi|"
    r"toronto|vancouver|montreal|sydney|melbourne|singapore|tokyo|seoul|"
    r"tel aviv|dubai|sao paulo|mexico city|"
    r"toulouse|abu dhabi|budapest|freiburg|"
    r"\b(uk|united kingdom|germany|sweden|netherlands|france|ireland|spain|"
    r"portugal|poland|switzerland|india|canada|australia|japan|israel|brazil|"
    r"mexico|austria|hungary|south korea)\b", re.I)

#: Below this a "body" is an apply link, not a job description, and rubric A
#: would score the title alone.
MIN_BODY_CHARS = 200


def geo_signal_for(location: str) -> str:
    """`us`, `non_us`, or `unknown`.

    `unknown` is a real answer, not a failure. Absence of a location is not
    evidence of a foreign one, and treating it as non-US silently discards
    the role.
    """
    text = (location or "").strip()
    if not text:
        return "unknown"
    # Strip accents before matching ("São Paulo" -> "Sao Paulo") so the
    # existing plain-ASCII foreign-city patterns actually fire against real
    # board data, which is not guaranteed to be ASCII.
    text = unicodedata.normalize("NFKD", text).encode(
        "ascii", "ignore").decode("ascii")
    # Non-US first: "Hybrid - London or Remote" must not pass on "Remote".
    if _NON_US_RE.search(text):
        return "non_us"
    if _US_WORDS_RE.search(text) or _US_ABBREV_RE.search(text):
        return "us"
    return "unknown"


def is_storable(role: AtsRole) -> bool:
    if geo_signal_for(role.location) == "non_us":
        return False
    return len(role.body or "") >= MIN_BODY_CHARS
