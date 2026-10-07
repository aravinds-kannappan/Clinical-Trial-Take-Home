"""Resolve ClinicalTrials.gov country names to ISO 3166-1 codes for map rendering."""

from __future__ import annotations

from functools import lru_cache

import pycountry

# Names used by ClinicalTrials.gov that pycountry does not match directly.
_OVERRIDES = {
    "Korea, Republic of": "KOR",
    "Korea, Democratic People's Republic of": "PRK",
    "Russian Federation": "RUS",
    "Iran, Islamic Republic of": "IRN",
    "Taiwan": "TWN",
    "Türkiye": "TUR",
    "Turkey": "TUR",
    "Vietnam": "VNM",
    "Viet Nam": "VNM",
    "Czechia": "CZE",
    "Czech Republic": "CZE",
    "Moldova, Republic of": "MDA",
    "Macedonia, The Former Yugoslav Republic of": "MKD",
    "North Macedonia": "MKD",
    "Bolivia": "BOL",
    "Venezuela": "VEN",
    "Tanzania": "TZA",
    "Syrian Arab Republic": "SYR",
    "Lao People's Democratic Republic": "LAO",
    "Congo, The Democratic Republic of the": "COD",
    "Côte D'Ivoire": "CIV",
    "Cote d'Ivoire": "CIV",
    "Hong Kong": "HKG",
    "Macao": "MAC",
    "Palestinian Territory, occupied": "PSE",
    "Palestinian Territories, Occupied": "PSE",
    "Brunei Darussalam": "BRN",
    "Micronesia, Federated States of": "FSM",
    "Kosovo": None,
    "Netherlands Antilles": None,
    "Former Serbia and Montenegro": None,
    "Former Yugoslavia": None,
}


@lru_cache(maxsize=512)
def country_codes(name: str) -> tuple[str | None, str | None]:
    """Return (alpha-3, numeric) for a country name, or (None, None) if unresolved."""
    if name in _OVERRIDES:
        alpha3 = _OVERRIDES[name]
        if alpha3 is None:
            return None, None
        c = pycountry.countries.get(alpha_3=alpha3)
        return (c.alpha_3, c.numeric) if c else (None, None)
    c = pycountry.countries.get(name=name) or pycountry.countries.get(official_name=name)
    if c is None:
        try:
            matches = pycountry.countries.search_fuzzy(name)
            c = matches[0] if matches else None
        except LookupError:
            c = None
    return (c.alpha_3, c.numeric) if c else (None, None)
