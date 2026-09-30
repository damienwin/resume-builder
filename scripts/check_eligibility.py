#!/usr/bin/env python3
"""Closed-set degree/years-of-experience eligibility extraction + local comparison.

Replaces job-scan Step 2.6's all-prose degree-eligibility judgment with a
deterministic function whose fail-open property is enforceable by a unit
test rather than dependent on an LLM re-reading prose correctly every run.

Design: `eval/efficiency/eligibility-redesign.md`.
Spec: `eval/efficiency/proposals-v2.md`, section 7 (degree-eligibility-hardening).

Extraction pipeline for one job description:

    1. Deterministic regex pre-filter (`regex_extract`) over the *public* JD
       text. Handles well-worn phrasings ("Bachelor's degree in...", "PhD
       required", "MS or PhD preferred") at zero cost. Degree tokens are
       word-bounded; an alternation list ("Bachelor's, Master's, or PhD",
       "BS/MS/PhD") resolves to its LOWEST level, because meeting the easier
       alternative satisfies an either/or statement. Anything compound or
       ambiguous (conflicting qualifiers, "and"-joined levels, "or
       equivalent", an MS/PhD requirement stated only implicitly) is NOT
       confident and is deferred. Only the degree fields decide confidence;
       years are filled best-effort because they never affect the status.
    2. Jev (`call_jev`, POST https://api.typesafe.ai/v1/systemone) for
       anything the regex defers -- only when the Jev gate is on (see
       `jev_gate`). Jev receives ONLY the public JD text as `state`. Its
       answer is then checked locally: the extracted degree level must be
       evidenced by a matching token in the JD text (otherwise
       extraction_ok=False), and it may feed the "ineligible" branch only if
       Jev's own probability for both degree answers clears
       `JEV_MIN_PROBABILITY` (otherwise `can_drop=False`). Any HTTP/network
       error, timeout, or malformed response -> extraction_ok=False, never a
       guess. Jev cannot return a verbatim quote, so source_span is null on
       this path.
    3. Jev gate off (no TYPESAFE_API_KEY, RESUME_BUILDER_JEV=off, or
       --no-jev): regex-deferred postings get status "needs_judgment" so
       job-scan Step 2.6 falls back to its in-context prose judgment for
       exactly those postings -- today's behavior, unchanged, for users
       without Jev.

The LOCAL comparison (`compare_eligibility`) reads only
`knowledge/education.md` and `knowledge/experience/*.md`. No network call;
nothing from `knowledge/` ever leaves the process.

Structural fail-open guarantee: `compare_eligibility` has exactly one
`return "ineligible"`, guarded solely by `is_hard_degree_mismatch`, whose
parameters contain no years data at all. That helper is true only when
extraction_ok and can_drop are true, degree_requirement == "required", and
the extracted degree is a known level strictly above the candidate's.
Years-required is surfaced as a stretch note only, per
`job-scan/references/acting-on-results.md` section 4a's standing "years is
never a stop condition" policy.

CLI:
    check_eligibility.py JD.txt [JD.txt ...] [--json] [--no-jev]
Exit 0: every JD has a result (per-JD failures are reported as
"unverified"). Non-zero exit: treat every posting as "unverified" (keep +
note).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

DEGREE_ORDER = {"none": 0, "BS": 1, "MS": 2, "PhD": 3}
DEGREE_VALUES = ("none", "BS", "MS", "PhD", "unknown")
REQUIREMENT_VALUES = ("required", "preferred", "unknown")
STATUSES = ("eligible", "ineligible", "unverified", "needs_judgment")

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_MODEL = "jev-latest"
JEV_TIMEOUT_SECONDS = 20
JEV_MIN_PROBABILITY = 0.8
JEV_DISABLE_ENV = "RESUME_BUILDER_JEV"

# Jev's `choice` type is a fixed enum, so years are bucketed; "not_stated"
# maps back to years_required=None.
YEARS_BUCKETS = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "10", "12", "15", "not_stated"]


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@dataclass
class ExtractionResult:
    degree: str = "unknown"
    degree_requirement: str = "unknown"
    years_required: Optional[int] = None
    years_requirement: str = "unknown"
    extraction_ok: bool = False
    source_span: Optional[str] = None
    # Not part of the frozen schema contract:
    # can_drop -- whether this record is trusted enough to feed the
    #   "ineligible" branch (regex: always, since it only emits confident
    #   drop-safe results; Jev: only above JEV_MIN_PROBABILITY).
    # needs_judgment -- regex deferred and Jev is gated off.
    # path -- which extractor produced the record, for logging.
    can_drop: bool = False
    needs_judgment: bool = False
    path: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "degree": self.degree,
            "degree_requirement": self.degree_requirement,
            "years_required": self.years_required,
            "years_requirement": self.years_requirement,
            "extraction_ok": self.extraction_ok,
            "source_span": self.source_span,
        }


# ---------------------------------------------------------------------------
# 1. Deterministic regex pre-filter
# ---------------------------------------------------------------------------

_CURLY_APOSTROPHE_RE = re.compile("[‘’ʼ]")
_DOTTED_PHD_RE = re.compile(r"(?<![A-Za-z])Ph\.?\s?D\.?(?![A-Za-z])", re.IGNORECASE)
_DOTTED_BM_RE = re.compile(r"(?<![A-Za-z])([BM])\.\s?([SA])\.?(?![A-Za-z])")


def normalize_text(text: str) -> str:
    """Curly apostrophes -> ASCII (web-pasted JDs use U+2019), and dotted
    degree abbreviations -> undotted ("Ph.D." -> "PhD", "B.S." -> "BS") so
    sentence splitting on "." doesn't cut through them."""
    text = _CURLY_APOSTROPHE_RE.sub("'", text)
    text = _DOTTED_PHD_RE.sub("PhD", text)
    text = _DOTTED_BM_RE.sub(lambda m: m.group(1) + m.group(2), text)
    return text


# Long forms are unambiguous; short forms (case-sensitive) need degree
# context. Both are letter-bounded so "systems", "algorithms", "MS Office"
# never read as a Master's requirement.
_LONG_TOKEN = r"(?i:bachelor(?:'s|s)?|master(?:'s|s)?|phd|doctorate|doctoral)"
_SHORT_TOKEN = r"(?:BSc|BS|BA|MSc|MS|MEng)"
_TOKEN_RE = re.compile(rf"(?<![A-Za-z])(?:{_LONG_TOKEN}|{_SHORT_TOKEN})(?![A-Za-z'])")
_LONG_ONLY_RE = re.compile(rf"^{_LONG_TOKEN}$")
_SEP_RE = re.compile(r"\s*(?:,\s*(?:or|and)?|/|or|and)\s*", re.IGNORECASE)
_SENTENCE_END_RE = re.compile(r"[.;!?\n]")

_SHORT_CONTEXT_AFTER_RE = re.compile(
    r"^\s*(?:degrees?\b|in\s|(?:is\s+)?required\b|preferred\b|or\s+equivalent\b)", re.IGNORECASE
)
_REQUIRED_AFTER_RE = re.compile(r"\b(?:required|must|minimum)\b", re.IGNORECASE)
_REQUIRED_BEFORE_TAIL_RE = re.compile(
    r"\b(?:requires?|required|must\s+(?:have|hold)|minimum(?:\s+of)?)\s*:?\s*(?:an?\s+)?$", re.IGNORECASE
)
_PREFERRED_RE = re.compile(
    r"\b(?:preferred|a\s+plus|nice[- ]to[- ]have|desired|desirable|bonus|ideally)\b", re.IGNORECASE
)
_PREFERRED_BEFORE_TAIL_RE = re.compile(r"\b(?:preferred|ideally)\s*:?\s*(?:an?\s+)?$", re.IGNORECASE)
_EQUIVALENT_RE = re.compile(r"\bor\s+(?:an?\s+)?equivalent\b", re.IGNORECASE)
_IN_FIELD_RE = re.compile(r"^\s*(?:degrees?\s+)?in\s+[A-Za-z]", re.IGNORECASE)
_NO_DEGREE_RE = re.compile(r"no degree required|degree not required|degree is not required", re.IGNORECASE)
# Used only to decide whether "no degree clause found" is itself confident.
_ANY_DEGREE_WORD_RE = re.compile(
    rf"(?<![A-Za-z])(?:{_LONG_TOKEN}|(?i:degrees?|diploma|undergrad(?:uate)?|postgrad(?:uate)?|graduate))(?![A-Za-z])"
)

_DEFER = object()


def _normalize_degree_token(token: str) -> str:
    t = token.strip().lower().replace(".", "").replace("'", "")
    if t.startswith("phd") or t.startswith("doctor"):
        return "PhD"
    if t.startswith("master") or t in ("ms", "msc", "meng"):
        return "MS"
    if t.startswith("bachelor") or t in ("bs", "ba", "bsc"):
        return "BS"
    return "unknown"


def _group_clauses(text: str) -> List[Dict[str, Any]]:
    """Groups consecutive degree tokens joined by list separators into
    clauses: "Bachelor's, Master's, or PhD" is one clause of three levels."""
    clauses: List[Dict[str, Any]] = []
    for m in _TOKEN_RE.finditer(text):
        if clauses:
            prev = clauses[-1]
            gap = text[prev["end"]:m.start()]
            if _SEP_RE.fullmatch(gap):
                prev["tokens"].append(m.group(0))
                prev["end"] = m.end()
                prev["has_and"] = prev["has_and"] or bool(re.search(r"\band\b", gap, re.IGNORECASE))
                continue
        clauses.append({"tokens": [m.group(0)], "start": m.start(), "end": m.end(), "has_and": False})
    return clauses


def _classify_clause(text: str, clause: Dict[str, Any], next_start: int):
    """Returns (level, requirement, span), None (not a degree mention), or
    _DEFER (a degree mention the regex can't confidently classify)."""
    start, end = clause["start"], clause["end"]
    sent_start = 0
    for m in _SENTENCE_END_RE.finditer(text, 0, start):
        sent_start = m.end()
    m_end = _SENTENCE_END_RE.search(text, end)
    sent_end = m_end.start() if m_end else len(text)
    after = text[end:min(sent_end, next_start)]
    before = text[sent_start:start]

    has_long = any(_LONG_ONLY_RE.match(t) for t in clause["tokens"])
    if not has_long and not _SHORT_CONTEXT_AFTER_RE.match(after):
        return None  # e.g. "MS Office" -- not a degree mention

    levels = {_normalize_degree_token(t) for t in clause["tokens"]} - {"unknown"}
    if not levels:
        return _DEFER
    if clause["has_and"] and len(levels) > 1:
        return _DEFER
    level = min(levels, key=lambda lv: DEGREE_ORDER[lv])
    span = (text[start:end] + after).strip()

    required = bool(_REQUIRED_AFTER_RE.search(after) or _REQUIRED_BEFORE_TAIL_RE.search(before))
    preferred = bool(_PREFERRED_RE.search(after) or _PREFERRED_BEFORE_TAIL_RE.search(before))
    if required and preferred:
        return _DEFER
    if _EQUIVALENT_RE.search(after):
        return _DEFER
    if preferred:
        return level, "preferred", span
    if required:
        return level, "required", span
    if _IN_FIELD_RE.match(after):
        # Implicit "Bachelor's degree in X" convention. Only trusted at BS
        # level (which can never drop a degree-holding candidate), and only
        # when no preferred-qualifications heading sits just above it.
        lookback = text[max(0, start - 300):start]
        if level == "BS" and not _PREFERRED_RE.search(lookback):
            return level, "required", span
        return _DEFER
    return _DEFER


def _regex_extract_degree(text: str) -> Tuple[bool, str, str, Optional[str]]:
    """Returns (confident, degree, degree_requirement, source_span). Expects
    `normalize_text` to have been applied."""
    clauses = _group_clauses(text)
    results = []
    for i, clause in enumerate(clauses):
        next_start = clauses[i + 1]["start"] if i + 1 < len(clauses) else len(text)
        r = _classify_clause(text, clause, next_start)
        if r is _DEFER:
            return False, "unknown", "unknown", None
        if r is not None:
            results.append(r)

    no_degree = _NO_DEGREE_RE.search(text)
    if no_degree:
        results.append(("none", "required", no_degree.group(0)))

    required = [r for r in results if r[1] == "required"]
    preferred = [r for r in results if r[1] == "preferred"]
    if required:
        if len({r[0] for r in required}) > 1:
            return False, "unknown", "unknown", None  # conflicting required levels
        level, _, span = required[0]
        return True, level, "required", span
    if preferred:
        level, _, span = min(preferred, key=lambda r: DEGREE_ORDER[r[0]])
        return True, level, "preferred", span

    if _ANY_DEGREE_WORD_RE.search(text):
        return False, "unknown", "unknown", None
    return True, "unknown", "unknown", None


_YEARS_RE = re.compile(
    r"(?P<span>(?P<num>\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*)?\+?\s*years?\s+"
    r"(?:of\s+)?(?:[A-Za-z-]+\s+){0,4}?experience)",
    re.IGNORECASE,
)


def _regex_extract_years(text: str) -> Tuple[Optional[int], str, Optional[str]]:
    """Best-effort (years_required, years_requirement, source_span). Years
    never affect the eligibility status, so a miss is just a missing note."""
    m = _YEARS_RE.search(text)
    if not m:
        return None, "unknown", None
    before = text[max(0, m.start() - 30):m.start()]
    after = text[m.end():m.end() + 20]
    requirement = "preferred" if (_PREFERRED_RE.search(before) or _PREFERRED_RE.search(after)) else "required"
    return int(m.group("num")), requirement, m.group("span").strip()


def regex_extract(jd_text: str) -> Optional[ExtractionResult]:
    """Returns None when the degree fields can't be confidently classified;
    callers then use Jev (gate on) or mark needs_judgment (gate off)."""
    text = normalize_text(jd_text)
    confident, degree, degree_requirement, deg_span = _regex_extract_degree(text)
    if not confident:
        return None
    years_required, years_requirement, years_span = _regex_extract_years(text)
    spans = [s for s in (deg_span, years_span) if s]
    return ExtractionResult(
        degree=degree,
        degree_requirement=degree_requirement,
        years_required=years_required,
        years_requirement=years_requirement,
        extraction_ok=True,
        source_span=" | ".join(spans) if spans else None,
        can_drop=True,
        path="regex",
    )


# ---------------------------------------------------------------------------
# 2. Jev gate + call
# ---------------------------------------------------------------------------


def _load_api_key() -> Optional[str]:
    """TYPESAFE_API_KEY from the environment, else from the repo-root .env.
    Never prints or logs the value."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if key:
        return key
    env_path = REPO_ROOT / ".env"
    try:
        lines = env_path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if name.strip() == "TYPESAFE_API_KEY":
            return value.strip().strip('"').strip("'") or None
    return None


def jev_gate(no_jev_flag: bool = False) -> Tuple[bool, str]:
    """(enabled, reason). Jev is optional: on only with a key, and
    force-disabled by --no-jev or RESUME_BUILDER_JEV=off."""
    if no_jev_flag:
        return False, "--no-jev"
    if os.environ.get(JEV_DISABLE_ENV, "").strip().lower() in ("off", "0", "false", "no"):
        return False, f"{JEV_DISABLE_ENV}=off"
    if not _load_api_key():
        return False, "TYPESAFE_API_KEY not set"
    return True, "enabled"


def build_jev_request(jd_text: str) -> Dict[str, Any]:
    """The exact POST /v1/systemone body. Contains ONLY the public JD text."""
    requirement_criteria = {
        "required": "Explicitly a minimum/required qualification.",
        "preferred": "Explicitly preferred, a plus, or nice-to-have, not required.",
        "unknown": "Not clearly stated either way.",
    }
    return {
        "state": jd_text,
        "model": JEV_MODEL,
        "questions": {
            "degree": {
                "type": "choice",
                "instructions": (
                    "What degree level is stated as a qualification for this "
                    "role? If the posting states a SINGLE degree level, "
                    "choose that level. If the posting states MULTIPLE "
                    "acceptable levels as alternatives (e.g. \"Bachelor's or "
                    "Master's degree\", \"BS/MS/PhD\"), choose the LOWEST of "
                    "those levels -- meeting the easier alternative already "
                    "satisfies an either/or statement, so the lowest stated "
                    "level is the true bar a candidate must clear. Choose "
                    "'unknown' if the posting does not clearly state one."
                ),
                "criteria": {
                    "none": "No degree is stated as a qualification.",
                    "BS": "Bachelor's degree (or equivalent, e.g. BA/BS).",
                    "MS": "Master's degree (or equivalent, e.g. MS/MEng).",
                    "PhD": "Doctorate / PhD.",
                    "unknown": "The posting does not clearly state a degree level.",
                },
            },
            "degree_requirement": {
                "type": "choice",
                "instructions": (
                    "Is the degree identified above stated as strictly "
                    "required, merely preferred/a plus, or not clearly "
                    "either?"
                ),
                "criteria": requirement_criteria,
            },
            "years_required": {
                "type": "choice",
                "instructions": (
                    "How many years of professional experience does the "
                    "posting state as a qualification? Choose the closest "
                    "stated minimum, or 'not_stated' if no years figure is "
                    "given."
                ),
                "criteria": {bucket: bucket for bucket in YEARS_BUCKETS},
            },
            "years_requirement": {
                "type": "choice",
                "instructions": (
                    "Is the years-of-experience figure identified above "
                    "stated as strictly required, merely preferred, or not "
                    "clearly either?"
                ),
                "criteria": dict(requirement_criteria, unknown="Not clearly stated either way, or no years figure is given."),
            },
        },
    }


# Word-bounded synonyms per level, matched after `normalize_text` (so
# "M.S."/"B.S."/"Ph.D." arrive undotted). "graduate"/"advanced degree"
# corroborate MS only, never PhD.
_DEGREE_EVIDENCE = {
    "BS": re.compile(
        r"(?<![A-Za-z])(?:(?i:bachelor(?:'s|s)?|undergraduate\s+degree|4-year\s+degree|b\.?\s?eng\.?)|BSc|BS|BA)(?![A-Za-z])"
    ),
    "MS": re.compile(
        r"(?<![A-Za-z])(?:(?i:master(?:'s|s)?|graduate\s+degree|graduate-level|advanced\s+degree|m\.?\s?eng\.?)"
        r"|MSc|MS)(?![A-Za-z])"
    ),
    "PhD": re.compile(r"(?<![A-Za-z])(?i:phd|doctorate|doctoral(?:\s+degree)?)(?![A-Za-z])"),
}


def degree_is_evidenced(degree: str, jd_text: str) -> bool:
    """Local corroboration: a Jev-extracted degree level must appear as a
    token in the JD itself. Levels with nothing to corroborate ("none",
    "unknown") pass -- they can never drop a posting."""
    pattern = _DEGREE_EVIDENCE.get(degree)
    return True if pattern is None else bool(pattern.search(normalize_text(jd_text)))


_EQUIVALENCE_QUALIFIER_RE = re.compile(
    r"\bor\s+(?:an?\s+)?(?:equivalent|comparable)\b"
    r"|\bequivalent\s+(?:\w+\s+){0,2}?(?:experience|education|training|qualifications?)\b"
    r"|\bin\s+lieu\s+of\b"
    r"|\bor\s+(?:related|relevant)\s+(?:practical\s+|work\s+|industry\s+)?experience\b",
    re.IGNORECASE,
)
_EQUIVALENCE_WINDOW = 200


def has_equivalence_qualifier(jd_text: str) -> bool:
    """True when an "or equivalent experience"-style qualifier sits near any
    degree token. A degree that is waivable by experience is not a hard
    requirement, so the Jev path must not drop on it. Deliberately
    over-triggers (keeping a posting is always the safe direction)."""
    text = normalize_text(jd_text)
    for pattern in _DEGREE_EVIDENCE.values():
        for m in pattern.finditer(text):
            window = text[max(0, m.start() - 60): m.end() + _EQUIVALENCE_WINDOW]
            if _EQUIVALENCE_QUALIFIER_RE.search(window):
                return True
    return False


def _answer_probability(answer: Dict[str, Any]) -> Optional[float]:
    probs = answer.get("probabilities")
    choice = answer.get("choice")
    if isinstance(probs, dict) and choice in probs and isinstance(probs[choice], (int, float)):
        return float(probs[choice])
    conf = answer.get("confidence")
    if isinstance(conf, (int, float)) and not isinstance(conf, bool):
        return float(conf)
    return None


def call_jev(jd_text: str, api_key: Optional[str] = None, timeout: int = JEV_TIMEOUT_SECONDS) -> ExtractionResult:
    """Calls Jev with ONLY the public JD text. Any failure -> extraction_ok
    False. Callers must check `jev_gate` first."""
    key = api_key if api_key is not None else _load_api_key()
    if not key:
        return ExtractionResult(extraction_ok=False, path="jev_no_key")

    req = urllib.request.Request(
        JEV_ENDPOINT,
        data=json.dumps(build_jev_request(jd_text)).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, ValueError):
        return ExtractionResult(extraction_ok=False, path="jev_error")

    try:
        answers = payload["answers"]
        degree_ans = answers["degree"]
        degree_req_ans = answers["degree_requirement"]
        degree = degree_ans["choice"]
        degree_requirement = degree_req_ans["choice"]
        years_bucket = answers["years_required"]["choice"]
        years_requirement = answers["years_requirement"]["choice"]
    except (KeyError, TypeError):
        return ExtractionResult(extraction_ok=False, path="jev_malformed")

    if (
        degree not in DEGREE_VALUES
        or degree_requirement not in REQUIREMENT_VALUES
        or years_requirement not in REQUIREMENT_VALUES
        or years_bucket not in YEARS_BUCKETS
    ):
        return ExtractionResult(extraction_ok=False, path="jev_malformed")

    if not degree_is_evidenced(degree, jd_text):
        return ExtractionResult(extraction_ok=False, path="jev_uncorroborated")

    if has_equivalence_qualifier(jd_text):
        return ExtractionResult(extraction_ok=False, path="jev_equivalence")

    probs = [_answer_probability(degree_ans), _answer_probability(degree_req_ans)]
    can_drop = all(p is not None and p >= JEV_MIN_PROBABILITY for p in probs)

    return ExtractionResult(
        degree=degree,
        degree_requirement=degree_requirement,
        years_required=None if years_bucket == "not_stated" else int(years_bucket),
        years_requirement=years_requirement,
        extraction_ok=True,
        source_span=None,
        can_drop=can_drop,
        path="jev",
    )


def extract(jd_text: str, jev_enabled: bool, api_key: Optional[str] = None) -> ExtractionResult:
    """Regex first; then Jev if the gate is on, else needs_judgment."""
    if not jd_text.strip():
        return ExtractionResult(extraction_ok=False, path="empty_jd")
    fast = regex_extract(jd_text)
    if fast is not None:
        return fast
    if not jev_enabled:
        return ExtractionResult(extraction_ok=False, needs_judgment=True, path="needs_judgment")
    return call_jev(jd_text, api_key=api_key)


# ---------------------------------------------------------------------------
# 3. Local comparison against knowledge/
# ---------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
_MONTH_YEAR_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{4})")


def _read_frontmatter(path: Path) -> Dict[str, str]:
    """Flat `key: value` frontmatter reader for knowledge/ files."""
    try:
        text = path.read_text()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    fields: Dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        key, _, value = line.partition(":")
        if key.strip() and value.strip():
            fields[key.strip()] = value.strip()
    return fields


def parse_user_degree(education_path: Path) -> Optional[str]:
    """education.md `degree:` -> BS/MS/PhD, or None if missing/ambiguous."""
    degree_field = _read_frontmatter(education_path).get("degree")
    if not degree_field:
        return None
    m = _TOKEN_RE.search(normalize_text(degree_field))
    if not m:
        return None
    level = _normalize_degree_token(m.group(0))
    return level if level in DEGREE_ORDER else None


def _parse_month_year(value: str) -> Optional[Tuple[int, int]]:
    m = _MONTH_YEAR_RE.search(value)
    if not m:
        return None
    month = _MONTHS.get(m.group(1)[:3].lower())
    return (int(m.group(2)), month) if month else None


def sum_years_experience(experience_dir: Path) -> Optional[float]:
    """Summed start/end durations from experience/*.md, for the stretch note
    only. Entries without a parseable end date are skipped, not guessed."""
    if not experience_dir.is_dir():
        return None
    total_months = 0
    found_any = False
    for path in sorted(experience_dir.glob("*.md")):
        fields = _read_frontmatter(path)
        start = _parse_month_year(fields.get("start", ""))
        end = _parse_month_year(fields.get("end", ""))
        if start is None or end is None:
            continue
        months = (end[0] - start[0]) * 12 + (end[1] - start[1])
        if months > 0:
            total_months += months
            found_any = True
    return round(total_months / 12.0, 1) if found_any else None


def is_hard_degree_mismatch(
    degree: str,
    degree_requirement: str,
    extraction_ok: bool,
    can_drop: bool,
    user_degree: Optional[str],
) -> bool:
    """The sole predicate that can lead to "ineligible". Takes no years data
    by construction."""
    return (
        extraction_ok is True
        and can_drop is True
        and degree_requirement == "required"
        and degree in DEGREE_ORDER
        and user_degree in DEGREE_ORDER
        and DEGREE_ORDER[degree] > DEGREE_ORDER[user_degree]
    )


def compare_eligibility(
    extraction: ExtractionResult,
    education_path: Path,
    experience_dir: Path,
) -> Tuple[str, str]:
    """(status, reason); status in STATUSES."""
    user_degree = parse_user_degree(education_path)

    if is_hard_degree_mismatch(
        extraction.degree,
        extraction.degree_requirement,
        extraction.extraction_ok,
        extraction.can_drop,
        user_degree,
    ):
        return "ineligible", f"required degree {extraction.degree} exceeds candidate's {user_degree}"

    notes: List[str] = []
    if extraction.years_required is not None and extraction.years_requirement != "unknown":
        stretch = (f"stretch: JD states {extraction.years_required}+ years "
                   f"({extraction.years_requirement}); not a stop condition")
        user_years = sum_years_experience(experience_dir)
        if user_years is not None:
            stretch += f" -- candidate has ~{user_years} years on file"
        notes.append(stretch)

    if extraction.needs_judgment:
        return "needs_judgment", "regex could not classify the degree requirement and Jev is disabled; use Step 2.6 prose judgment"
    if not extraction.extraction_ok:
        notes.insert(0, f"advanced-degree flag unverified ({extraction.path}); keeping posting per fail-open policy")
        return "unverified", "; ".join(notes)
    if user_degree is None:
        notes.insert(0, "education.md degree missing or ambiguous; advanced-degree flag unverified")
        return "unverified", "; ".join(notes)
    if extraction.degree_requirement == "required" and extraction.degree in DEGREE_ORDER:
        if DEGREE_ORDER[extraction.degree] <= DEGREE_ORDER[user_degree]:
            notes.insert(0, "required degree at or below candidate's level")
            return "eligible", "; ".join(notes)
        notes.insert(0, f"{extraction.degree} appears required but extraction confidence is too low to drop")
        return "unverified", "; ".join(notes)
    if extraction.degree_requirement == "preferred" and extraction.degree in DEGREE_ORDER:
        notes.insert(0, f"{extraction.degree} preferred, not required")
        return "unverified", "; ".join(notes)
    notes.insert(0, "degree requirement unverified or not a hard requirement")
    return "unverified", "; ".join(notes)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _check_one(jd_path: str, jev_enabled: bool, education: Path, experience_dir: Path, force_jev: bool) -> Dict[str, Any]:
    try:
        jd_text = Path(jd_path).read_text(encoding="utf-8", errors="replace")
        if force_jev and jev_enabled:
            extraction = call_jev(jd_text)
        else:
            extraction = extract(jd_text, jev_enabled=jev_enabled)
        status, reason = compare_eligibility(extraction, education, experience_dir)
        return {"jd_path": jd_path, "path": extraction.path, "extraction": extraction.to_dict(),
                "status": status, "reason": reason}
    except Exception as exc:  # fail open per posting
        return {"jd_path": jd_path, "path": "error", "extraction": ExtractionResult().to_dict(),
                "status": "unverified", "reason": f"eligibility check error ({type(exc).__name__}); keeping posting"}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Degree/years eligibility check for job-scan Step 2.6.")
    parser.add_argument("jd_paths", nargs="+", help="Cached JD .txt file(s)")
    parser.add_argument("--education", default=str(REPO_ROOT / "knowledge" / "education.md"))
    parser.add_argument("--experience-dir", default=str(REPO_ROOT / "knowledge" / "experience"))
    parser.add_argument("--no-jev", action="store_true", help="Disable Jev (same as RESUME_BUILDER_JEV=off)")
    parser.add_argument("--force-jev", action="store_true", help="Skip the regex pre-filter (only if Jev is enabled)")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        jev_enabled, gate_reason = jev_gate(no_jev_flag=args.no_jev)
        if not jev_enabled:
            print(f"check_eligibility: Jev disabled ({gate_reason}); postings the regex can't classify "
                  f"are marked needs_judgment.", file=sys.stderr)
        results = [
            _check_one(p, jev_enabled, Path(args.education), Path(args.experience_dir), args.force_jev)
            for p in args.jd_paths
        ]
    except Exception as exc:
        results = [{"jd_path": p, "path": "error", "extraction": ExtractionResult().to_dict(),
                    "status": "unverified", "reason": f"eligibility check error ({type(exc).__name__}); keeping posting"}
                   for p in args.jd_paths]
        jev_enabled = False
        _emit(results, jev_enabled, args.json)
        return 2

    _emit(results, jev_enabled, args.json)
    return 0


def _emit(results: List[Dict[str, Any]], jev_enabled: bool, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"jev_enabled": jev_enabled, "results": results}, indent=2))
        return
    for r in results:
        print(f"{r['jd_path']}: {r['status']} [{r['path']}] -- {r['reason']}")


if __name__ == "__main__":
    sys.exit(main())
