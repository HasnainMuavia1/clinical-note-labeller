from __future__ import annotations

from dataclasses import dataclass, field

from .dictionaries import CodeDictionaries
from .evidence import context_of, score_candidate
from .patterns import extract_candidates


@dataclass(frozen=True)
class CodeHit:
    code: str
    kind: str
    start: int
    end: int
    dictionary_hit: bool
    rule: str
    score: float
    context: str


@dataclass(frozen=True)
class DocumentCodeResult:
    has_codes: bool
    total_score: float
    hits: list[CodeHit] = field(default_factory=list)
    rejected: list[CodeHit] = field(default_factory=list)
    npis: list[str] = field(default_factory=list)


def detect_codes(text: str, dicts: CodeDictionaries, threshold: float = 1.0) -> DocumentCodeResult:
    hits: list[CodeHit] = []
    rejected: list[CodeHit] = []
    npis: list[str] = []
    total = 0.0

    for candidate in extract_candidates(text):
        if candidate.kind == "npi":
            if candidate.text not in npis:
                npis.append(candidate.text)
            continue

        score, rule = score_candidate(text, candidate, dicts)
        hit = CodeHit(
            code=candidate.text,
            kind=candidate.kind,
            start=candidate.start,
            end=candidate.end,
            dictionary_hit=dicts.contains(candidate.text) is not None,
            rule=rule,
            score=score,
            context=context_of(text, candidate).replace("\n", " ").strip(),
        )
        if score > 0:
            hits.append(hit)
            total += score
        else:
            rejected.append(hit)

    substantive = sum(h.score for h in hits if h.kind != "modifier")
    return DocumentCodeResult(
        has_codes=substantive >= threshold,
        total_score=round(total, 3),
        hits=hits,
        rejected=rejected,
        npis=npis,
    )
