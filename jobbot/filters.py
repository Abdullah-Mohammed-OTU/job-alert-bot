"""Decide which postings are worth a ping, and which tech category they fall in."""
from __future__ import annotations

import re
from dataclasses import dataclass

I = re.IGNORECASE

# Checked in this order; the first match names the category, so specific
# categories come before the broad "software" catch-all ("Data Engineer" is Data,
# "Security Engineer" is Security, "Software Developer in Test" is QA).
CATEGORIES: dict[str, str] = {
    "security": (
        r"\b(security|cyber\w*|infosec|soc|penetration|pen ?test\w*|appsec|threat|vulnerability|"
        r"incident response|grc|identity (and|&) access|iam)\b"
    ),
    "data": (
        r"\b(data|analytics|business intelligence|bi|machine learning|ml|mlops|ai|artificial intelligence|"
        r"deep learning|nlp|computer vision|statistic(s|ian)|quant(itative)?|data scientist|"
        r"research scientist|applied scientist|reporting analyst|insights analyst)\b"
    ),
    "qa": r"\b(qa|quality assurance|test(ing)?|tester|sdet|test automation|automation|qe|quality engineer(ing)?)\b",
    "it": (
        r"(?-i:\bIT\b)|\b(information technology|i&it|help ?desk|service desk|desktop support|"
        r"technical support|tech support|it support|application support|production support|"
        r"systems? administrat(or|ion)|sysadmin|network(ing)?|infrastructure|cloud|devops|dev ops|"
        r"sre|site reliability|platform|database|dba|systems? (engineer|specialist)|"
        r"technical (analyst|specialist)|end[- ]user|endpoint|servicenow|erp|sap|"
        r"(computer|network|desktop|support|service desk|field service|technical|electronics|telecom\w*) technician)\b"
    ),
    "product": (
        r"\b(associate product manager|apm|product analyst|program analyst|business systems analyst|"
        r"business analyst|systems analyst|solutions analyst|technology (analyst|consultant|associate)|"
        r"it consultant|implementation (analyst|specialist|consultant)|ux|ui|user experience|"
        r"user interface|product designer|interaction designer|technical writer)\b"
    ),
    "software": (
        r"\b(software|developer|programmer|programming|swe|sde|full[- ]?stack|back[- ]?end|front[- ]?end|"
        r"web|mobile|ios|android|game|embedded|firmware|engineer(ing)?|technology|computer science)\b"
    ),
}
CATEGORY_LABELS = {
    "security": "Security", "data": "Data / AI", "qa": "QA / Test", "it": "IT / Infra",
    "product": "Product / BA / Design", "software": "Software",
}

DEFAULT_EXCLUDE = (
    r"\b(senior|sr\.?|staff|principal|lead|director|head|architect|vp|distinguished|"
    r"intern(ship)?|co-?op|summer|phd|ii|iii|iv|2|3|4|l[4-9]|"
    r"sales|account|recruit\w*|customer|"
    r"mechanical|electrical|civil|chemical|structural|process|manufacturing|mining|geotechnical|"
    r"nurse|nursing|pharmac\w*|medical|laboratory|radiation|"
    r"security (guard|officer)|loss prevention)\b"
    # "manager" is out, except Associate Product Manager (a new-grad track).
    r"|(?<!associate product )\bmanager\b"
)
DEFAULT_JUNIOR = (
    r"(\bnew[- ]?grad(uate)?\b|\bgraduate\b|\bentry[- ]level\b|\bjunior\b|\bjr\b|\bearly[- ]career\b|"
    r"\bearly talent\b|\bassociate\b|\bcampus\b|\brotational\b|\bdevelopment program\b|\btrainee\b|"
    r"\bapprentice\b|\b(tier|level) 1\b|\bapm\b|"
    r"\b(engineer|developer|programmer|analyst|specialist|administrator|technician)\s+(i|1)\b|\b20(26|27)\b)"
)
# City names match any case; province codes only in capitals so "on" the word doesn't count.
DEFAULT_CANADA = (
    r"(?i:canada|toronto|ontario|waterloo|kitchener|ottawa|mississauga|markham|oshawa|"
    r"montr[eé]al|qu[eé]bec|vancouver|calgary|edmonton|winnipeg|halifax)|\b(ON|BC|QC|AB|MB|NS)\b"
)
# Clearly outside North America. "Toronto / London" still passes because
# Canada is checked first.
DEFAULT_BLOCK = (
    r"london|\buk\b|united kingdom|england|dublin|ireland|germany|berlin|munich|paris|france|"
    r"amsterdam|netherlands|spain|madrid|barcelona|poland|warsaw|portugal|lisbon|sweden|"
    r"switzerland|zurich|israel|tel aviv|india|bangalore|bengaluru|hyderabad|pune|mumbai|delhi|"
    r"gurgaon|chennai|singapore|japan|tokyo|korea|seoul|china|shanghai|beijing|hong kong|taiwan|"
    r"australia|sydney|melbourne|new zealand|brazil|s[aã]o paulo|argentina|colombia|mexico|"
    r"philippines|vietnam|emea|apac|latam|europe"
)


@dataclass
class Filter:
    categories: list[tuple[str, re.Pattern]]
    exclude: re.Pattern
    junior: re.Pattern
    canada: re.Pattern
    block: re.Pattern
    include_us: bool
    require_junior_keyword: bool

    @classmethod
    def from_settings(cls, s: dict) -> "Filter":
        enabled = s.get("categories", list(CATEGORIES))
        unknown = set(enabled) - set(CATEGORIES)
        if unknown:
            raise SystemExit(f"config: unknown categories {sorted(unknown)}; choose from {list(CATEGORIES)}")
        overrides = s.get("category_patterns", {})
        # Keep the built-in order even if the config lists them differently.
        cats = [(name, re.compile(overrides.get(name, CATEGORIES[name]), I))
                for name in CATEGORIES if name in enabled]
        return cls(
            categories=cats,
            exclude=re.compile(s.get("exclude_pattern", DEFAULT_EXCLUDE), I),
            junior=re.compile(s.get("junior_pattern", DEFAULT_JUNIOR), I),
            canada=re.compile(s.get("canada_pattern", DEFAULT_CANADA)),
            block=re.compile(s.get("block_pattern", DEFAULT_BLOCK), I),
            include_us=bool(s.get("include_us", True)),
            require_junior_keyword=bool(s.get("require_junior_keyword", False)),
        )

    def is_junior(self, title: str) -> bool:
        return bool(self.junior.search(title))

    def category(self, title: str) -> str | None:
        """Category label for a wanted title, or None if it should be skipped."""
        if self.exclude.search(title):
            return None
        if self.require_junior_keyword and not self.is_junior(title):
            return None
        for name, pat in self.categories:
            if pat.search(title):
                return CATEGORY_LABELS[name]
        return None

    def title_ok(self, title: str) -> bool:
        return self.category(title) is not None

    def location_ok(self, loc: str) -> bool:
        if not loc:
            return True  # unknown: keep rather than miss something
        if self.canada.search(loc):
            return True
        if self.block.search(loc):
            return False
        if self.include_us:
            return True  # US, US-remote, or unspecified remote
        return bool(re.search(r"remote", loc, I))

    def matches(self, job) -> bool:
        return self.title_ok(job.title) and self.location_ok(job.location)
