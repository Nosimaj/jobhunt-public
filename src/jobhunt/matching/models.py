from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ..models import Strict

Bucket = Literal["strong", "maybe", "no"]
Discipline = Literal["design", "product", "engineering", "growth", "other"]
HybridSignal = Literal["explicit", "contextual", "none"]

#: Values that satisfy `concern`'s length floor while defeating its purpose.
_NON_CONCERNS = frozenset({
    "none", "n/a", "na", "nothing", "no concerns", "none noted",
    "none really", "no concern", "nothing significant", "nothing major",
})


class MatchVerdict(Strict):
    """One verdict for one ROLE inside one posting. Judge the role, then
    classify it; do not pick a score or a bucket and justify it afterwards."""

    # Field order is the generation order, so `reason` and `concern` come
    # before every classification field, and `fit_score` comes AFTER `bucket`
    # so it cannot be used to smuggle enthusiasm past the bucket definitions.
    #
    # This ordering is a nudge, not a guarantee: adaptive thinking is on by
    # default, so the model has usually reached its conclusion in a thinking
    # block before it emits any field at all. The load-bearing guards are the
    # validator below, `concern`'s minimum length, and the rubric itself.
    #
    # Field `description=` values DO reach the model (the SDK folds them into
    # the JSON schema), so they are written as instructions. `ge`/`le` and
    # `min_length` reach it only as advisory prose appended to the
    # description - `transform_schema` demotes the keywords it cannot express,
    # so they do NOT constrain generation. They act as post-hoc REJECTION, and
    # a violation costs the whole verdict. So every one of them is also stated
    # in prose, and the floors are set low enough that legitimate output
    # cannot trip them: `concern`'s floor exists only to block "None." and a
    # real short concern like "Salary is not disclosed." (24 chars) must pass.
    role_title: str = Field(
        min_length=1,
        description="The exact title of the single best-fitting role in this "
                    "posting, copied verbatim. Not the company name.")
    discipline: Discipline = Field(
        description="The discipline that role belongs to. Use `engineering` "
                    "for any engineering-titled role even when the posting is "
                    "from a tiny company.")
    reason: str = Field(
        min_length=1,
        description="Why this role fits or does not, in one or two sentences, "
                    "citing the posting.")
    concern: str = Field(
        min_length=10,
        description="The single strongest argument AGAINST applying, as one "
                    "sentence. Required for every bucket including `strong`. "
                    "'None' is not a concern.")
    hybrid_signal: HybridSignal = Field(
        description="Whether the posting signals a need for someone who spans "
                    "design and engineering: `explicit`, `contextual`, or "
                    "`none`. `none` forbids bucket `strong`.")
    bucket: Bucket = Field(
        description="The ACTION, not the enthusiasm: `strong` = worth an hour "
                    "writing a tailored application, `maybe` = worth two "
                    "minutes' reading before deciding, `no` = would not apply.")
    fit_score: int = Field(
        ge=0, le=100,
        description="0-100, and it must agree with the bucket: `no` is 0-39, "
                    "`maybe` is 40-69, `strong` is 70-100.")
    location: str | None = Field(
        default=None,
        description="The location the ROLE is offered in, copied verbatim from "
                    "the posting, or null. Never the company's headquarters "
                    "and never inferred.")
    salary: str | None = Field(
        default=None,
        description="The compensation as written in the posting, or null. "
                    "Never estimated.")

    @model_validator(mode="after")
    def _no_strong_without_hybrid_signal(self) -> "MatchVerdict":
        # Spec 3.1: the hybrid gate. A role that signals no cross-discipline
        # need caps at `maybe`, because the user loses to specialists unless
        # the role specifically wants both hats. Enforced here so no code path
        # can bypass it.
        if self.hybrid_signal == "none" and self.bucket == "strong":
            raise ValueError(
                "hybrid_signal 'none' cannot be bucket 'strong': the hybrid gate "
                "caps such roles at 'maybe' (spec 3.1)")
        # The actual failure mode `concern`'s length floor is guarding against,
        # named precisely instead of proxied by a character count. A length
        # bar high enough to catch "None noted" would also discard legitimate
        # one-clause concerns, and a rejection costs the whole verdict.
        if self.concern.strip().strip(".!").casefold() in _NON_CONCERNS:
            raise ValueError(
                f"concern {self.concern!r} is not a concern: every role has "
                "one (spec 5.3)")
        return self


class Label(Strict):
    """One frozen human verdict from tests/fixtures/matching/eval_labels.yaml."""
    id: str
    company: str
    verdict: Bucket
    criterion: str


class Disagreement(Strict):
    id: str
    company: str
    human: Bucket
    model_bucket: Bucket
    severe: bool          # strong<->no inversion
    reason: str
    criterion: str


class EvalReport(Strict):
    total: int
    exact: int
    agreement: float
    confusion: dict[str, dict[str, int]]
    #: Labels the model never scored. Counted as disagreements AND as severe:
    #: a run that skipped the only `strong` label has not demonstrated it can
    #: find one, and must not be allowed to report a pass for the 24 it did.
    unscored: int = 0
    #: The balance check. `positive_total` is the number of labels the human
    #: did NOT mark `no`; `positive_hit` is how many of those the model also
    #: kept out of `no`. Exact agreement alone cannot fail a prompt that says
    #: `no` to everything, because ~80% of the labels ARE `no`.
    positive_total: int = 0
    positive_hit: int = 0
    disagreements: list[Disagreement] = Field(default_factory=list)

    @property
    def severe_count(self) -> int:
        return sum(1 for d in self.disagreements if d.severe)

    @property
    def positive_recall(self) -> float:
        return (self.positive_hit / self.positive_total
                if self.positive_total else 0.0)


# ---------------------------------------------------------------------------
# Two-axis fit (slice 3 amendment). Role fit and company fit are scored
# independently and combined by `fit.derive_action`. The models below carry
# the guards that cannot be trusted to a prompt.
# ---------------------------------------------------------------------------

RoleFit = Literal["strong", "partial", "no"]
CompanyFit = Literal["target", "neutral", "no"]
HybridTest = Literal["pass", "weak", "fail", "n/a"]
DomainFit = Literal["direct", "adjacent", "none"]
RoleDiscipline = Literal["design", "design_engineering", "engineering",
                         "product", "other"]
Signal = Literal["present", "absent", "unknown"]


class RoleVerdict(Strict):
    """Whether THIS POSTING's role is one they can credibly win and would want.

    Score the role, not the company. A great company with the wrong opening is
    a `no` here - company appeal is scored separately and must not influence
    this verdict. There is deliberately no field on this model in which company
    appeal could be recorded.
    """

    role_title: str = Field(
        min_length=1,
        description="The exact title of the single best-fitting role in this "
                    "posting, copied verbatim. Not the company name.")
    discipline: RoleDiscipline = Field(
        description="The discipline that role belongs to. Use `engineering` "
                    "for any engineering-titled role even when the posting is "
                    "from a tiny company; `design_engineering` only when the "
                    "posting itself frames the role as spanning both.")
    reason: str = Field(
        min_length=1,
        description="Why this role fits or does not, in one or two sentences, "
                    "citing the posting.")
    hybrid_test: HybridTest = Field(
        description="For engineering roles: `pass` if the posting assigns a "
                    "qualifying duty, `weak` if one duty qualifies only "
                    "marginally, `fail` otherwise. `n/a` for non-engineering "
                    "roles. Titles are NOT evidence.")
    hybrid_evidence: list[str] = Field(
        default_factory=list,
        description="Exact duty phrases COPIED FROM THE POSTING that qualify "
                    "it as hybrid. Required when hybrid_test is `pass`. Quote "
                    "the posting; never paraphrase and never infer from a "
                    "title or from stack overlap.")
    domain_fit: DomainFit = Field(
        description="How closely this ROLE's subject matter matches their "
                    "record: `direct` if the posting's own duties sit in one "
                    "of the target domains, `adjacent` if they neighbour one, "
                    "`none` otherwise. Judge the ROLE, not the company - an "
                    "internal-tools role at a company that sells something "
                    "else is still `direct`.")
    domain_evidence: list[str] = Field(
        default_factory=list,
        description="Exact duty phrases COPIED FROM THE POSTING that place it "
                    "in a target domain. Required when domain_fit is "
                    "`direct` or `adjacent`. Quote the posting; never paraphrase, and never "
                    "infer a domain from the company's name or industry.")
    overlaps: list[str] = Field(
        default_factory=list,
        description="Concrete requirement-to-record matches, each naming the "
                    "requirement and the evidence.")
    gaps: list[str] = Field(
        default_factory=list,
        description="Concrete gaps between the posting's requirements and their "
                    "record.")
    role_fit: RoleFit = Field(
        description="`strong` = right discipline, a clear hybrid pass, or "
                    "(engineering) a `direct` domain match, with seniority "
                    "in range and at least two concrete overlaps. "
                    "`partial` = right discipline with one real gap, a weak "
                    "hybrid pass, or an `adjacent`-only domain match. "
                    "`no` = wrong discipline, an engineering role with "
                    "neither a hybrid pass nor a domain match, or a hard "
                    "requirement they cannot meet.")
    seniority_note: str | None = Field(
        default=None,
        description="Only if seniority is out of range, in a few words. "
                    "Otherwise null.")
    location: str | None = Field(
        default=None,
        description="The location the ROLE is offered in, copied verbatim, or "
                    "null. Never the company headquarters, never inferred.")
    salary: str | None = Field(
        default=None,
        description="Compensation as written in the posting, or null. Never "
                    "estimated.")

    @model_validator(mode="after")
    def _relevance_is_the_gate(self) -> "RoleVerdict":
        """The relevance floor, enforced in code because a prompt cannot be
        trusted with it. Engineering roles ONLY; design roles pass on
        discipline alone.

        An engineering role must earn its place by a hybrid pass (or weak
        pass) OR a domain match (`direct`/`adjacent`); with neither it must be
        `no`. Caps: a weak hybrid, or an `adjacent`-only match with no hybrid
        pass, tops out at `partial` - only a hybrid pass or a `direct` match
        may be `strong`. `direct` and `pass` need quoted evidence.
        (Background: the 2026-09-28 eval scored Biobase `strong` on stack
        resemblance alone, which is why resemblance is not a free pass.)
        """
        if self.discipline == "engineering":
            # CHANGED 2026-10-06: an either/or floor, not a hybrid floor. They
            # lose engineering roles to career engineers unless the posting
            # wants both hats OR the work is in their domains. Scoped to
            # engineering: a domain gate on design roles would cut good ones.
            # A `weak` hybrid still counts as earning a place (it always did,
            # capped at partial below); only `fail`/`n/a` need the domain.
            earned = (self.hybrid_test in ("pass", "weak")
                      or self.domain_fit in ("direct", "adjacent"))
            if not earned and self.role_fit != "no":
                raise ValueError(
                    f"an engineering role with neither a hybrid pass nor a "
                    f"domain match must be role_fit 'no', got "
                    f"{self.role_fit!r}: they lose these to career engineers")
            if self.hybrid_test == "weak" and self.role_fit == "strong":
                raise ValueError(
                    "a weak hybrid pass caps role_fit at 'partial'")
            if (self.role_fit == "strong" and self.hybrid_test != "pass"
                    and self.domain_fit != "direct"):
                raise ValueError(
                    "an adjacent-only domain match caps role_fit at "
                    "'partial': only a hybrid pass or a direct domain match "
                    "may be 'strong'")
        if self.hybrid_test == "pass" and not self.hybrid_evidence:
            raise ValueError(
                "hybrid_test 'pass' requires hybrid_evidence quoted from the "
                "posting: a title is not evidence")
        if self.domain_fit == "direct" and not self.domain_evidence:
            raise ValueError(
                "domain_fit 'direct' requires domain_evidence quoted from the "
                "posting: an industry is not evidence")
        return self


class CompanyVerdict(Strict):
    """Whether the COMPANY is worth a conversation, independent of any opening.

    Cached per company: the answer does not change between two postings from
    the same employer, and re-deriving it per posting invites drift. Note the
    cache saves little within one HN thread (measured: 131 companies across
    132 candidates) - its value is across months, and in consistency.
    """

    reason: str = Field(
        min_length=1,
        description="One or two sentences on what the company builds and "
                    "whether that pulls them in.")
    domain_pull: Signal = Field(
        description="Does the company work in one of their target domains? "
                    "`unknown` when the source text does not say - silence is "
                    "not absence.")
    designs_how_things_work: Signal = Field(
        description="Does it create or improve something people use - a "
                    "product, service, machine, system or process? Absent only "
                    "when the core business resells attention or labour "
                    "(ad-tech, performance marketing, staffing).")
    design_valued: Signal = Field(
        description="Evidence it hires designers, talks about craft or UX, or "
                    "has design-led products. No design function at all is a "
                    "NEGATIVE signal, not a neutral one.")
    stage_fit: Signal = Field(
        description="Is it at a stage or scale where a senior generalist "
                    "matters?")
    reachability: Signal = Field(
        description="Boston or remote-friendly, or a leadership team plausibly "
                    "reachable for outreach.")
    disqualifier: str | None = Field(
        default=None,
        description="The disqualifying fact, quoted, or null. Any disqualifier "
                    "forces company_fit `no`.")
    company_fit: CompanyFit = Field(
        description="`target` = domain pull present AND at least two of the "
                    "other four signals present, no disqualifier. `neutral` = "
                    "nothing disqualifying but domain pull absent or mostly "
                    "unknown. `no` = a disqualifier, or both "
                    "designs_how_things_work and design_valued clearly absent.")

    @property
    def _supporting(self) -> int:
        return sum(1 for s in (self.designs_how_things_work, self.design_valued,
                               self.stage_fit, self.reachability)
                   if s == "present")

    @model_validator(mode="after")
    def _target_must_be_earned(self) -> "CompanyVerdict":
        # `target` is the verdict that promotes a rejected role into an
        # outreach lead, so it is the expensive one to get wrong.
        if self.disqualifier and self.company_fit != "no":
            raise ValueError(
                f"disqualifier {self.disqualifier!r} forces company_fit 'no'")
        if (self.designs_how_things_work == "absent"
                and self.design_valued == "absent" and self.company_fit != "no"):
            raise ValueError(
                "designs_how_things_work and design_valued both absent forces "
                "company_fit 'no'")
        if self.company_fit == "target":
            if self.domain_pull != "present":
                raise ValueError(
                    f"company_fit 'target' requires domain_pull 'present', got "
                    f"{self.domain_pull!r}")
            if self._supporting < 2:
                raise ValueError(
                    f"company_fit 'target' requires at least two of the other "
                    f"four signals present, got {self._supporting}")
        return self
