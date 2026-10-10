from __future__ import annotations

from pathlib import Path

import yaml

from .models import Bucket, Disagreement, EvalReport, Label, MatchVerdict

AGREEMENT_BAR = 0.60

#: The balance bar. Of the labels the human did NOT mark `no`, this share must
#: come back as something other than `no`.
#:
#: Without it the instrument is one-sided. 20 of the 25 labels are `no`, so a
#: prompt that answered `no` to everything and `maybe` to the single `strong`
#: posting scores 19/25 = 76% with ZERO severe inversions - a pass, for a
#: prompt that can never surface a job. The rubric is deliberately loaded
#: toward `no`, so over-deflation is the likelier failure, not inflation.
POSITIVE_BAR = 0.60
BUCKETS: tuple[str, ...] = ("strong", "maybe", "no")
_SEVERE = {frozenset({"strong", "no"})}


def load_labels(path: Path) -> list[Label]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return [Label(**row) for row in doc["labels"]]


def _is_severe(human: str, model_bucket: str) -> bool:
    """strong<->no is an inversion and the real failure. strong<->maybe is noise."""
    return frozenset({human, model_bucket}) in _SEVERE


def evaluate(labels: list[Label], verdicts: dict[str, MatchVerdict]) -> EvalReport:
    confusion = {h: {m: 0 for m in BUCKETS} for h in BUCKETS}
    disagreements: list[Disagreement] = []
    exact = 0
    unscored = 0
    positive_total = sum(1 for lab in labels if lab.verdict != "no")
    positive_hit = 0

    for label in labels:
        got = verdicts.get(label.id)
        if got is None:
            # An unscored label is a disagreement, not a crash and not a pass,
            # and it is SEVERE. A per-posting API failure is a normal
            # condition, so without this a failure on the one `strong` label
            # yields 24/25, zero severe, PASS - a pass earned by not
            # attempting the only question that mattered.
            unscored += 1
            disagreements.append(Disagreement(
                id=label.id, company=label.company, human=label.verdict,
                model_bucket="no", severe=True,
                reason="<not scored>", criterion=label.criterion))
            continue
        if label.verdict != "no" and got.bucket != "no":
            positive_hit += 1
        confusion[label.verdict][got.bucket] += 1
        if got.bucket == label.verdict:
            exact += 1
        else:
            disagreements.append(Disagreement(
                id=label.id, company=label.company, human=label.verdict,
                model_bucket=got.bucket, severe=_is_severe(label.verdict, got.bucket),
                reason=got.reason, criterion=label.criterion))

    total = len(labels)
    return EvalReport(total=total, exact=exact,
                      agreement=(exact / total if total else 0.0),
                      confusion=confusion, unscored=unscored,
                      positive_total=positive_total, positive_hit=positive_hit,
                      disagreements=disagreements)


def passed(report: EvalReport) -> bool:
    """The gate, defined once. Three independent conditions: enough exact
    agreement, no inversions, and a prompt that is not simply deflationary."""
    return (report.agreement >= AGREEMENT_BAR
            and report.severe_count == 0
            # Vacuous when the label set contains no non-`no` rows at all -
            # there is nothing to recall. The real 25-label set has 5, so this
            # only ever short-circuits in synthetic tests.
            and (report.positive_total == 0
                 or report.positive_recall >= POSITIVE_BAR))


def render_report(report: EvalReport) -> str:
    lines = [
        "# Matching eval",
        "",
        f"exact agreement : {report.exact}/{report.total} "
        f"({report.agreement:.0%})   bar is {AGREEMENT_BAR:.0%}",
        f"severe (strong<->no, or not scored) : {report.severe_count}   bar is 0",
        (f"kept out of `no` : {report.positive_hit}/{report.positive_total} of the "
         f"non-`no` labels ({report.positive_recall:.0%})   bar is {POSITIVE_BAR:.0%}"
         if report.positive_total else "kept out of `no` : n/a (no non-`no` labels)"),
        f"not scored : {report.unscored}",
        "",
        f"RESULT: {'PASS' if passed(report) else 'FAIL'}",
        "",
        "## Confusion (rows = human, columns = model)",
        "",
        "| human \\ model | " + " | ".join(BUCKETS) + " |",
        "|---|" + "---|" * len(BUCKETS),
    ]
    for h in BUCKETS:
        lines.append(f"| {h} | " + " | ".join(str(report.confusion[h][m])
                                              for m in BUCKETS) + " |")
    lines += ["", "## Disagreements", ""]
    if not report.disagreements:
        lines.append("_None._")
    for d in report.disagreements:
        flag = " **SEVERE**" if d.severe else ""
        lines += [f"### {d.company} (`{d.id}`){flag}",
                  f"- human: **{d.human}** - {d.criterion}",
                  f"- model: **{d.model_bucket}** - {d.reason}", ""]
    # 25 labels is a rough instrument: it catches a prompt that says yes to
    # everything, but cannot separate 70% agreement from 75%.
    lines.append("_25 labels is a rough instrument. It catches a prompt that says yes "
                 "to everything (severe inversions) and one that says no to everything "
                 "(the balance bar); it cannot distinguish 70% agreement from 75%. Do "
                 "not tune past its resolution._")
    return "\n".join(lines)
