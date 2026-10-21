"""Normalize candidates without guessing year, territory or table semantics."""

import hashlib, re
from .core import parse_number

UNIT = re.compile(
    r"(мг/дм[³3]|мг/л|мкг/л|мг/кг|км[²2³3]?|м[³3²2]|га|тонн|тыс\.\s*т|%)$"
)
TERRITORIES = {
    "озеро Байкал": r"озер[ао]\s+Байкал",
    "Республика Бурятия": r"Республик[аи]\s+Буряти",
    "Иркутская область": r"Иркутск[ао]й?\s+област",
    "река Селенга": r"рек[аи]\s+Селенг",
}


def normalize(candidate, source, method):
    raw = candidate["raw"]
    quote = candidate["quote"]
    unit = UNIT.search(raw)
    scalar = parse_number(raw[: unit.start()].strip()) if unit else None
    years = set(
        int(y) for y in re.findall(r"\b(20(?:0\d|1\d|2[0-6]))\s*(?:г[.о]|год)", quote)
    )
    territories = [
        name for name, pattern in TERRITORIES.items() if re.search(pattern, quote, re.I)
    ]
    key = f"{source['sha256']}:{candidate['page']}:{quote}:{raw}"
    return {
        "id": hashlib.sha256(key.encode()).hexdigest()[:24],
        "year": next(iter(years)) if len(years) == 1 else None,
        "report_year": source.get("report_year"),
        "indicator": None,
        "raw_value": raw,
        "value": scalar,
        "unit": unit[0] if unit else None,
        "territory": territories[0] if len(territories) == 1 else None,
        "source_id": source["id"],
        "sha256": source["sha256"],
        "page": candidate["page"],
        "quote": quote,
        "source_url": source["url"],
        "method": method + " + unit/context regex",
        "confidence": 0.35,
        "review_status": "candidate",
        "period": None,
        "comparability_group": None,
        "comparability_approved": False,
        "issues": ["Indicator and observation scope require review"]
        + (["Non-scalar or qualified value"] if scalar is None else []),
    }
