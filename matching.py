"""Description matching and exact scalar operations shared by the audit pipeline.

Matching never uses billed prices or quantities. HistoryFallbackMatcher retains
an already accepted identity before applying broader historical families.
"""
import copy
from datetime import date
from difflib import SequenceMatcher
from functools import lru_cache
import re


ALIASES = {
    "adv": "advanced", "amb": "ambulatory", "asst": "assisted", "ass": "assisted",
    "inpt": "inpatient", "outpt": "outpatient", "intens": "intensive", "intermit": "intermittent",
    "preop": "preoperative", "postop": "postoperative", "rtn": "routine", "std": "standard",
    "spec": "specialist", "sup": "supervised", "ext": "extended", "foc": "focused",
    "msk": "musculoskeletal", "neuro": "neurological", "ophth": "ophthalmic", "onc": "oncology",
    "ger": "geriatric", "derm": "dermatologic", "pulm": "pulmonary", "ren": "renal",
    "otol": "otolaryngologic", "haem": "haematology", "immun": "immunologic",
    "svc": "service", "sess": "session", "occ": "occupancy", "rm": "room", "bd": "bed",
    "wd": "ward", "cr": "care", "recov": "recovery", "physio": "physiotherapy",
    "dial": "dialysis", "inf": "infusion", "transf": "transfusion", "nutr": "nutritional",
    "supp": "support", "hm": "home", "cs": "case", "biop": "biopsy", "lab": "laboratory",
}


def tokens(text, extra_aliases=None):
    text = re.sub(r"/[^\s]+", " ", text.lower())
    aliases = {**ALIASES, **(extra_aliases or {})}
    return set(aliases.get(t, t) for t in re.findall(r"[a-z]+", text))


def parse_date(value):
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return None
        return date.fromisoformat(value)
    except ValueError:
        return None


def integer(value):
    return type(value) is int


def usable_line_id(value):
    """Only actual nonblank identifiers can determine within-day order."""
    return isinstance(value, str) and bool(value.strip())


def rounded_ratio(amount, numerator, denominator):
    """Exact half-away-from-zero rounding, with integer-only intermediates."""
    product = amount * numerator
    return (1 if product >= 0 else -1) * ((2 * abs(product) + denominator) // (2 * denominator))


class Matcher:
    def __init__(self, services, overrides=None, guidance=None):
        self.names = [s["service_name"] for s in services]
        self.guidance = guidance
        self.aliases = guidance.get("token_aliases", {}) if guidance else {}
        self.words = [tokens(name, self.aliases) for name in self.names]
        self.reviewed = {tuple(sorted(tokens(r["description"], self.aliases))): r
                         for r in (guidance or {}).get("reviewed_descriptions", [])}
        self.overrides = overrides or {}
        if any(value not in self.names for value in self.overrides.values()):
            raise ValueError("Service override is not in this contract")

    @lru_cache(maxsize=None)
    def match(self, description):
        query = tokens(description, self.aliases)
        if self.guidance:
            record = self.reviewed.get(tuple(sorted(query)))
            constrained = [name for name, words in zip(self.names, self.words) if query <= words] if len(query) >= 2 else []
            candidates = record["candidates"] if record else constrained
            if candidates:
                return {"service_name": candidates[0] if len(candidates) == 1 else None,
                        "similarity": None, "margin": None, "status": "accepted" if len(candidates) == 1 else "review",
                        "method": "reviewed_description" if record else "full_catalogue_token_constraints",
                        "candidate_scope_complete_under_description_assumption": True,
                        "evidence": record["evidence"] if record else "All catalogue services containing every normalised description token; not based on billed amounts or units.",
                        "candidates": [{"service_name": name, "similarity": None} for name in candidates]}
            families = [f for f in self.guidance.get("history_only_service_families", [])
                        if set(f["tokens"]) <= query]
            if families:
                # A UNION is conservative if multiple family phrases occur.
                # Even one candidate is NOT an accepted service identity here:
                # the original full description did not match the catalogue.
                scoped = [name for name, words in zip(self.names, self.words)
                          if any(set(f["tokens"]) <= words for f in families)]
                return {"service_name": None, "similarity": None, "margin": None, "status": "review",
                        "method": "history_only_service_family", "outside_catalogue_possible": True,
                        "candidate_scope_complete_under_description_assumption": True,
                        "evidence": " ".join(f["evidence"] for f in families),
                        "candidates": [{"service_name": n, "similarity": None} for n in scoped]}
        if description in self.overrides:
            return {"service_name": self.overrides[description], "similarity": None, "margin": None,
                    "status": "accepted", "method": "ai_proposed_catalogue_mapping",
                    "candidates": [{"service_name": self.overrides[description], "similarity": None}]}
        scores = []
        for name, candidate in zip(self.names, self.words):
            # Token matching tolerates word order, abbreviation and truncated words.
            pairs = sorted(((self.token_score(a, b), a, b) for a in query for b in candidate), reverse=True)
            used_a, used_b, total = set(), set(), 0.0
            for score, a, b in pairs:
                if a not in used_a and b not in used_b:
                    used_a.add(a)
                    used_b.add(b)
                    total += score
            score = 2 * total / (len(query) + len(candidate)) if query else 0
            scores.append((score, name))
        scores.sort(reverse=True)
        best, runner = scores[0][0], scores[1][0] if len(scores) > 1 else 0
        accepted = best >= 0.84 and best - runner >= 0.12
        return {"service_name": scores[0][1] if accepted else None, "similarity": round(best, 4),
                "margin": round(best - runner, 4), "status": "accepted" if accepted else "review",
                "candidates": [{"service_name": n, "similarity": round(s, 4)} for s, n in scores[:3]]}

    @staticmethod
    @lru_cache(maxsize=65536)
    def token_score(a, b):
        if a == b:
            return 1.0
        if min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)):
            return 0.94
        ratio = SequenceMatcher(None, a, b).ratio()
        return ratio if ratio >= 0.8 else 0.0


class HistoryFallbackMatcher(Matcher):
    """New families are a fallback, never replacements for existing matches."""
    def __init__(self, services, overrides=None, guidance=None):
        super().__init__(services, overrides, guidance)
        prior_guide = copy.deepcopy(guidance)
        if prior_guide is not None:
            prior_guide["history_only_service_families"] = prior_guide.get(
                "pre_overlay_history_families", prior_guide.get("history_only_service_families", []))
        self.prior_matcher = Matcher(services, overrides, prior_guide)

    def match(self, description):
        prior = self.prior_matcher.match(description)
        return prior if prior["service_name"] is not None else super().match(description)


def history_family_scope(description, services, aliases=None, single_word_families=False):
    """Conditional volume-history scope, never an identity or unit conversion.

    Use complete two-word family phrases from this catalogue only. Multiple
    families are unioned; unexplained family words prevent narrowing.
    """
    families = {}
    for service in services:
        name = service['service_name']
        words = re.findall(r'[a-z]+', name.lower())
        if single_word_families and len(words) == 3 and words[-1] == 'consultation':
            families.setdefault(frozenset({'consultation'}), []).append(name)
        if single_word_families and words[-2:] == ['case', 'conference']:
            families.setdefault(frozenset({'conference'}), []).append(name)
        if len(words) < 4:
            continue
        key = frozenset(tokens(' '.join(words[-2:]), aliases))
        if len(key) == 2:
            families.setdefault(key, []).append(name)
    query = tokens(description, aliases)
    # History-only normalization: accept a truncation only when it has exactly
    # one completion anywhere in this hospital's catalogue. Never use amounts,
    # fuzzy edit distance, or these expansions to assert a service identity.
    catalogue_words = set().union(*(tokens(s['service_name'], aliases) for s in services)) if services else set()
    expansions = {}
    for word in query - catalogue_words:
        completions = [candidate for candidate in catalogue_words
                       if len(word) >= 4 and candidate.startswith(word)]
        if len(completions) == 1:
            expansions[word] = completions[0]
    query = {expansions.get(word, word) for word in query}
    # A reviewed paired abbreviation, only for history-family bounds. "OBS"
    # alone is insufficient; do not infer specialty, exact service, or unit.
    if {'nursing', 'obs'} <= query and frozenset({'nursing', 'observation'}) in families:
        expansions['obs'] = 'observation'
        query = (query - {'obs'}) | {'observation'}
    if (query & {'lab', 'laboratory'} and query & {'pnl', 'panel'}
            and frozenset({'laboratory', 'panel'}) in families):
        for short, full in (('lab', 'laboratory'), ('pnl', 'panel')):
            if short in query:
                expansions[short] = full
                query = (query - {short}) | {full}
    matched = [family for family in families if family <= query]
    if not matched:
        return None
    covered = set().union(*matched)
    vocabulary = set().union(*families)
    if (query & vocabulary) - covered:
        return None
    return {
        'candidate_services': sorted({name for family in matched for name in families[family]}),
        'family_tokens': sorted(sorted(family) for family in matched),
        'family_token_expansions': expansions,
        'assumption': 'The explicit service-family words describe the historical service truthfully; clinical modifiers and exact identity remain unresolved, and an outside-catalogue service remains possible.',
        'basis': 'complete_catalogue_family_phrases_for_volume_bounds_only',
    }


def variant_key(description):
    """Keep lexical variants distinct; do not expand abbreviations or reorder."""
    return " ".join(re.findall(r"[a-z]+", re.sub(r"/[^\s]+", " ", description.lower())))
