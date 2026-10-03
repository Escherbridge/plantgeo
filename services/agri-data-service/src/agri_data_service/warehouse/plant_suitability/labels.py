"""Per-pick label text and the fire guards (phrase claims at ingress, fire-family stems and scripts at egress).

Rationale, the normalisation steps, the stems and the plant-name exemptions: AGENTS.md §Labels in this directory.
"""

from __future__ import annotations

import functools
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

import polars as pl

from agri_data_service.warehouse.plant_suitability.axes import AXIS_NAMES

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

FIRE_LABEL_PREFIX = "PLANTS fire-resistant label (not a fire claim; judged against California fires): "
IN_REGION_YES = "in-region: yes"
IN_REGION_NO = "in-region: no — neighbouring guide"
UNCERTAIN_FROST_FREE = "uncertain frost-free fit"
CONFIRMED_FROST_FREE = "frost-free fit: confirmed"
UNKNOWN_FROST_FREE = "frost-free fit: unknown (no PLANTS minimum)"
SITE_FROST_FREE_MISSING = "frost-free fit: unknown (site frost-free days missing)"
UNCHECKABLE_SOURCES = "sources (band or habitat not checkable here): "
STRATIFICATION_PREFIX = "site stratification from "
UNCHECKED_PREFIX = "unchecked: "
GUILDS_WITH_FIRE_LABEL = ("greenstrip", "post_fire_restoration")
NO_FIRE_CLAIM = "Nothing in this layer claims that any plant prevents, slows or reduces fire."
# Full-word name of each axis in the "unchecked: ..." label segment.
AXIS_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "cold": "cold hardiness",
        "dry_year_precip": "dry-year precipitation",
        "mean_precip": "annual precipitation",
        "ph": "soil pH",
        "frost_free_days": "frost-free days",
        "texture": "soil texture",
        "root_depth": "rooting depth",
        "salinity": "salinity",
        "calcareous": "calcium carbonate",
        "anaerobic": "soil wetness",
        "drought": "drought",
        "wetland_indicator": "wetland indicator",
        "source_applicability": "guide band or habitat",
    }
)
# Normalisation (AGENTS.md §Labels): NFKD, drop these categories (whitespace controls become a space), NFC, fold.
DROPPED_CATEGORIES = frozenset({"Mn", "Me", "Cf", "Cc"})
# Letters drawn blank that Unicode marks default-ignorable: the Hangul fillers.
BLANK_LETTERS = "\u115f\u1160\u3164\uffa0"
UNICODE_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d\u2027"
# Letters drawn like Latin ones: a subset of the Unicode UTS #39 confusables skeleton
# (https://www.unicode.org/reports/tr39/#Confusable_Detection), Cyrillic, Greek, Armenian, Latin and Coptic.
LOOKALIKE_LETTERS = (
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u043a\u04bb\u0501\u051b\u051d"  # Cyrillic small
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0406\u0408\u0405\u0423"  # Cyrillic capital
    "\u03bf\u03b1\u03b9\u03ba\u03bd\u03c1\u03c5"  # Greek small
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7"  # Greek capital
    "\u0585\u0555\u0251\u0131\u0269\u2c9f\u2c9e\u04cf\u04c0"  # Armenian, Latin, Coptic, Cyrillic palochka
)
LATIN_LETTERS = (
    "aeopcyxijskhdqw"  # Cyrillic small
    "ABEKMHOPCTXIJSY"  # Cyrillic capital
    "oaikvpu"  # Greek small
    "ABEZHIKMNOPTYX"  # Greek capital
    "oOaiioOiI"  # Armenian, Latin, Coptic, Cyrillic palochka
)
# The palochka is drawn like both I and l, so the guards also read each text with it as "l".
I_OR_L_LETTERS = "\u04cf\u04c0"
PALOCHKA_AS_L = str.maketrans(dict.fromkeys(I_OR_L_LETTERS, "l"))
FOLD_TABLE = str.maketrans(
    {**dict.fromkeys(UNICODE_DASHES, "-"), **dict(zip(LOOKALIKE_LETTERS, LATIN_LETTERS, strict=True))}
)
WHITESPACE_RUN = re.compile(r"\s+")
# Label segments: the " | "-joined fields and the " + "-joined citations inside them.
SEGMENT_SEPARATOR = re.compile(r" \| | \+ ")
# Word boundaries that treat "_" as a separator, and the separator between words.
START, END, SEPARATOR, WORD_TAIL = r"(?<![^\W_])", r"(?![^\W_])", r"[\W_]", r"[^\W_]*"
WORD = re.compile(r"[^\W_]+")
CLAIM_VERBS = (
    rf"(?:prevent|reduc|slow|stop|suppress|retard|lower|limit|halt|block|decreas|curb|mitigat|protect|inhibit"
    rf"|contain|fight|hinder|imped|deter|lessen|minimi[sz]|combat){WORD_TAIL}"
)
BURN_FORMS = rf"(?:burn(?:s|ed|ing|t)?{END}|ignit{WORD_TAIL}|combust{WORD_TAIL})"
FIRE_NOUNS = (
    rf"(?:(?:wild)?fires?|flames?|flammab{WORD_TAIL}|ignit{WORD_TAIL}|fuels?|sparks?|embers?"
    rf"|burn(?:s|ed|ing|t)?|combust{WORD_TAIL})"
)
PROOFING_STEMS = (
    rf"(?:resist|retard|proof|safe|wise|smart|slow|stop|block|halt|suppress|prevent|reduc|limit|resilien|hardy)"
    rf"{WORD_TAIL}"
)
# The ingress claim guard (AGENTS.md §Labels): a claim verb within six words of a fire noun; "keeps fire from / away /
# at bay"; a fire-proofing compound; "resistant to fire"; low / less / non flammability; slow-burning and hard to
# ignite; "resists the spread of fire"; less fire-prone; a barrier ("firebreak", "fire barrier", "barrier / buffer /
# shield against fire").
FIRE_CLAIM_PATTERN = re.compile(
    rf"{START}{CLAIM_VERBS}{SEPARATOR}+(?:[^\W_]+{SEPARATOR}+){{0,6}}?{FIRE_NOUNS}{END}"
    rf"|{START}keeps?{SEPARATOR}+(?:[^\W_]+{SEPARATOR}+){{0,2}}?{FIRE_NOUNS}{SEPARATOR}+"
    rf"(?:from|away|out|back|at{SEPARATOR}+bay){END}"
    rf"|{START}(?:wild)?(?:fire|flame){SEPARATOR}*{PROOFING_STEMS}"
    rf"|{START}(?:resist|withstand){WORD_TAIL}{SEPARATOR}+(?:to{SEPARATOR}+)?(?:(?:the|a){SEPARATOR}+)?{FIRE_NOUNS}{END}"
    rf"|{START}(?:low(?:er|est)?|less|least|non){SEPARATOR}*flammab"
    rf"|{START}(?:slow|low|less|non|hard|poor){SEPARATOR}*(?:to{SEPARATOR}+)?{BURN_FORMS}"
    rf"|{START}{BURN_FORMS}{SEPARATOR}+(?:slow|poor|reluctant){WORD_TAIL}"
    rf"|{START}resist{WORD_TAIL}{SEPARATOR}+(?:the{SEPARATOR}+)?spread{SEPARATOR}+of{SEPARATOR}+(?:(?:the|a){SEPARATOR}+)?"
    rf"{FIRE_NOUNS}{END}"
    rf"|{START}(?:less|least|not|non|low(?:er)?){SEPARATOR}+(?:wild)?(?:fire|flame){SEPARATOR}*prone"
    rf"|{START}(?:fire|fuel){SEPARATOR}*breaks?{END}"
    rf"|{START}(?:wild)?fire{SEPARATOR}*(?:barrier|shield|wall)s?{END}"
    rf"|{START}(?:barrier|buffer|shield)s?{SEPARATOR}+(?:against|to|from){SEPARATOR}+(?:(?:the|a){SEPARATOR}+)?"
    rf"{FIRE_NOUNS}{END}"
    rf"|{START}defensible{SEPARATOR}*space{END}",
    re.IGNORECASE,
)
# The only texts ingress lets pair fire words with those verbs: the verbatim PLANTS label prefix and the disclaimer.
FIRE_CLAIM_ALLOW_LIST = (FIRE_LABEL_PREFIX, NO_FIRE_CLAIM)
# The egress guard's fire-family stems, refused anywhere inside a word ("brushfire", "unburnable", "flammables");
# "ember" only at a word's start, so member, September and remember pass.
FIRE_STEMS = (
    "fire", "flam", "burn", "ignit", "combust", "fuel", "spark", "blaz", "conflagr", "smoulder", "smolder", "scorch",
    "extinguish", "pyro",
)  # fmt: skip
WORD_START_STEMS = ("ember",)
FIRE_STEM_PATTERN = re.compile("|".join([*FIRE_STEMS, *(f"^{stem}" for stem in WORD_START_STEMS)]), re.IGNORECASE)
# The closed list of plant names holding a stem, removed whole-word before the stem check (AGENTS.md §Labels).
PLANT_NAME_EXEMPTIONS = (
    "fireweed", "firewheel", "firethorn", "firecracker", "swampfire", "burnet", "burningbush", "burning bush",
    "burnweed", "blazingstar", "blazing star", "flameflower", "flammula", "viburnum", "viburnifolium", "agropyron",
    "diospyros", "pyrola", "pyrolaceae", "pyroliflora", "pyrolifolia",
)  # fmt: skip
PLANT_NAME_EXEMPTION_PATTERN = re.compile(
    rf"{START}(?:{'|'.join(name.replace(' ', f'{SEPARATOR}+') for name in PLANT_NAME_EXEMPTIONS)}){END}",
    re.IGNORECASE,
)
# Single letters spaced or dotted out ("f i r e", "f.i.r.e"), read as one word.
SPELLED_OUT_LETTERS = re.compile(r"(?<![^\W_])[^\W\d_](?:[\W_]{1,3}[^\W\d_](?![^\W_])){2,}")
# Digits and signs written for letters inside a token that holds a letter ("f1re", "8urn", "f|re"; an inverted "!"
# or a broken bar for i, a euro or pound sign for e).
LETTER_SUBSTITUTES = str.maketrans("0134578|!$@\u00a1\u00a6\u20ac\u00a3", "oieastbiisaiiee")
TOKEN = re.compile(r"\S+")
LETTER = re.compile(r"[^\W\d_]")
# Punctuation between two letters ("fi.re", "fi-re", a middle dot, "fir'e"), removed for the joined reading. Egress
# keeps ";": the engine joins top-3 names with it, and "grand fir;European alder" would read "firEuropean".
INTRA_WORD_PUNCTUATION = re.compile(r"(?<=[^\W\d_])[^\w\s]+(?=[^\W\d_])")
EGRESS_INTRA_WORD_PUNCTUATION = re.compile(r"(?<=[^\W\d_])[^\w\s;]+(?=[^\W\d_])")
# Signs written for an unknown letter ("f*re", "f?re"): a word holding one is matched against the stems with the sign
# standing for any letter, when at least MASKED_STEM_MIN_LETTERS of the matched letters are real.
MASK_SIGNS = "*?"
MASKED_WORD = re.compile(r"(?:[^\W_]|[*?])*[*?](?:[^\W_]|[*?])*")
MASKED_STEM_MIN_LETTERS = 2
# Egress refuses letters, digits and other or currency symbols beyond Latin-1 left after normalisation: none of them
# can be read as a Latin word (Latin Extended, IPA small capitals, Greek epsilon, regional indicators, the euro sign).
LATIN_1_LAST = 0xFF
OUT_OF_SCRIPT_CATEGORIES = frozenset({"Lu", "Ll", "Lt", "Lm", "Lo", "Nd", "Nl", "No", "So", "Sc"})
# Texts every served table may carry whatever the registry: the label prefix, the disclaimer, the guild names.
FIXED_ALLOWED_TEXTS = (FIRE_LABEL_PREFIX, NO_FIRE_CLAIM, "greenstrip", "post_fire_restoration", "hedgerow_buffer")
# The woody buffer carries no fire field: the PLANTS label prefix, or any "Fire Resistant" field text.
FIRE_FIELD_PATTERN = re.compile(rf"{START}(?:wild)?fire{SEPARATOR}*resist", re.IGNORECASE)


@functools.cache
def dropped_characters() -> dict[int, str | None]:
    """Translate table: every mark, format or control character removed, whitespace controls turned into a space."""
    table: dict[int, str | None] = dict.fromkeys(map(ord, BLANK_LETTERS))
    for code in range(sys.maxunicode + 1):
        character = chr(code)
        if unicodedata.category(character) in DROPPED_CATEGORIES:
            table[code] = " " if character.isspace() else None
    return table


def normalised_text(text: str) -> str:
    """NFKD; marks, format and control characters dropped; NFC; dashes and lookalikes folded; one space per run."""
    stripped = unicodedata.normalize("NFKD", text).translate(dropped_characters())
    folded = unicodedata.normalize("NFC", stripped).translate(FOLD_TABLE)
    return WHITESPACE_RUN.sub(" ", folded)


def normalised_readings(text: str) -> tuple[str, ...]:
    """The normalised text, plus its reading with every palochka as "l" when it holds one."""
    reading = normalised_text(text)
    if not any(letter in text for letter in I_OR_L_LETTERS):
        return (reading,)
    return reading, normalised_text(text.translate(PALOCHKA_AS_L))


def segments(text: str) -> list[str]:
    """The text split into label fields and citations, so no match spans two of them."""
    return SEGMENT_SEPARATOR.split(text)


def fire_resistance_label(values: list[str] | None) -> str:
    """Yes / No / conflicting / unknown from every PLANTS 'Fire Resistant' value of the taxon and merged members."""
    distinct = set(values or [])
    if not distinct:
        return "unknown"
    if distinct in ({"Yes"}, {"No"}):
        return next(iter(distinct))
    return "conflicting"


def frost_free_expression() -> pl.Expr:
    """Uncertain, confirmed, unknown for want of site frost-free days, else unknown for want of a PLANTS minimum."""
    site_missing = pl.col("median_frost_free_days").is_null() | pl.col("frost_free_days_station_bias").is_null()
    return (
        pl.when(pl.col("frost_free_uncertain"))
        .then(pl.lit(UNCERTAIN_FROST_FREE))
        .when(pl.col("axis_frost_free_days") == 1)
        .then(pl.lit(CONFIRMED_FROST_FREE))
        .when(site_missing)
        .then(pl.lit(SITE_FROST_FREE_MISSING))
        .otherwise(pl.lit(UNKNOWN_FROST_FREE))
    )


def unchecked_axes_expression() -> pl.Expr:
    """'unchecked: <axis>, ...' naming each unknown axis in AXIS_NAMES order; null when every axis is known."""
    unknown = [pl.when(pl.col(f"axis_{name}").is_null()).then(pl.lit(AXIS_LABELS[name])) for name in AXIS_NAMES]
    names = pl.concat_list(unknown).list.drop_nulls()
    return pl.when(names.list.len() > 0).then(pl.lit(UNCHECKED_PREFIX) + names.list.join(", "))


def appended(label: pl.Expr, segment: pl.Expr) -> pl.Expr:
    """The label with ' | <segment>' appended when the segment is not null."""
    return pl.when(segment.is_null()).then(label).otherwise(pl.concat_str([label, segment], separator=" | "))


def pick_label_expression(guild: str, *, label_unchecked_axes: bool) -> pl.Expr:
    """' | '-joined label: fire field, origin, in-region, frost-free fit, names, sources, stratification, unchecked."""
    fire_label = pl.lit(FIRE_LABEL_PREFIX) + pl.col("fire_resistance_label")
    fire_field = [fire_label] if guild in GUILDS_WITH_FIRE_LABEL else []
    sources_prefix = (
        pl.when(pl.col("axis_source_applicability").is_null())
        .then(pl.lit(UNCHECKABLE_SOURCES))
        .otherwise(pl.lit("sources: "))
    )
    base = pl.concat_str(
        [
            *fire_field,
            pl.col("origin_label"),
            pl.when(pl.col("in_region_supported")).then(pl.lit(IN_REGION_YES)).otherwise(pl.lit(IN_REGION_NO)),
            frost_free_expression(),
            pl.lit("listed as: ") + pl.col("supporting_names").list.join(", "),
            sources_prefix + pl.col("supporting_tags").list.join(" + "),
        ],
        separator=" | ",
    )
    stratification = pl.when(pl.col("stratification_sources").list.len() > 0).then(
        pl.lit(STRATIFICATION_PREFIX) + pl.col("stratification_sources").list.join(" + ")
    )
    label = appended(base, stratification)
    return appended(label, unchecked_axes_expression()) if label_unchecked_axes else label


def find_fire_claims(texts: Iterable[str | None]) -> list[str]:
    """Every text in which some segment claims a fire effect once normalised and stripped of the allowed wording."""

    def claims(text: str) -> bool:
        for reading in normalised_readings(text):
            remaining = functools.reduce(
                lambda kept, allowed: kept.replace(allowed, " "), FIRE_CLAIM_ALLOW_LIST, reading
            )
            for segment in segments(remaining):
                if any(FIRE_CLAIM_PATTERN.search(read) for read in (segment, *derived_readings(segment))):
                    return True
        return False

    return [text for text in texts if text and claims(text)]


@dataclass(frozen=True)
class FireTextAllowList:
    """The texts egress removes before its token check, matched whole-word and longest first."""

    texts: tuple[str, ...]

    @functools.cached_property
    def pattern(self) -> re.Pattern[str] | None:
        """One alternation of the normalised texts holding a fire word (whole-word removal, so no other hides one)."""
        stripped = {normalised_text(text).strip() for text in self.texts}
        normalised = sorted((text for text in stripped if has_fire_token(text)), key=len, reverse=True)
        alternatives = [
            f"{START if WORD.match(text[0]) else ''}{re.escape(text)}{END if WORD.match(text[-1]) else ''}"
            for text in normalised
        ]
        return re.compile("|".join(alternatives)) if alternatives else None

    def without_allowed(self, reading: str) -> str:
        """The normalised text with every allowed text replaced by a space."""
        return reading if self.pattern is None else self.pattern.sub(" ", reading)


def served_allow_list(registered_texts: Iterable[str]) -> FireTextAllowList:
    """The fixed allowed texts plus the engine's registered ones (source names and ids, flag texts)."""
    return FireTextAllowList((*FIXED_ALLOWED_TEXTS, *registered_texts))


def substituted_token(match: re.Match[str]) -> str:
    """The token with digits and signs read as letters, when it holds a letter; else unchanged."""
    token = match.group()
    return token.translate(LETTER_SUBSTITUTES) if LETTER.search(token) else token


def spelled_reading(text: str) -> str:
    """The text with spelled-out single letters joined, then digits and signs inside words read as letters."""
    joined = SPELLED_OUT_LETTERS.sub(lambda match: "".join(WORD.findall(match.group())), text)
    return TOKEN.sub(substituted_token, joined)


def joined_reading(text: str, punctuation: re.Pattern[str] = INTRA_WORD_PUNCTUATION) -> str:
    """The text with punctuation between two letters removed ("fi.re", "fi-re" read "fire")."""
    return punctuation.sub("", text)


def derived_readings(text: str) -> tuple[str, ...]:
    """Ingress readings: the spelled text, plus the spelled joined text when punctuation sits between letters."""
    spelled = spelled_reading(text)
    if not INTRA_WORD_PUNCTUATION.search(text):
        return (spelled,)
    return spelled, spelled_reading(joined_reading(text))


def reads_as_masked_stem(word: str) -> bool:
    """True when the word holds a stem with each mask sign standing for any letter and enough real letters matched."""
    lowered = word.casefold()
    for stem in (*FIRE_STEMS, *WORD_START_STEMS):
        last_start = len(lowered) - len(stem)
        starts = range(min(last_start, 0) + 1) if stem in WORD_START_STEMS else range(last_start + 1)
        for start in starts:
            window = lowered[start : start + len(stem)]
            real_letters = sum(character not in MASK_SIGNS for character in window)
            matched = all(character in (letter, *MASK_SIGNS) for character, letter in zip(window, stem, strict=True))
            if matched and real_letters >= MASKED_STEM_MIN_LETTERS:
                return True
    return False


def without_plant_names(text: str) -> str:
    """The text with every plant-name exemption replaced by a space (whole-word)."""
    return PLANT_NAME_EXEMPTION_PATTERN.sub(" ", text)


def fire_words(text: str) -> list[str]:
    """Every word holding a fire-family stem in any reading (spelled, joined, masked), once exemptions are removed."""
    # Exemptions go before joining too, so "Agropyron/Elymus" never reads as one unexempted word.
    unexempted = without_plant_names(text)
    readings = [spelled_reading(text)]
    if EGRESS_INTRA_WORD_PUNCTUATION.search(unexempted):
        readings.append(spelled_reading(joined_reading(unexempted, EGRESS_INTRA_WORD_PUNCTUATION)))
    if any(sign in text for sign in MASK_SIGNS):
        readings.append(text)
    found: list[str] = []
    for reading in readings:
        kept = without_plant_names(reading)
        found.extend(word for word in WORD.findall(kept) if FIRE_STEM_PATTERN.search(word))
        found.extend(word for word in MASKED_WORD.findall(kept) if reads_as_masked_stem(word))
    return found


def has_fire_token(text: str) -> bool:
    """True when a word of the text holds a fire-family stem outside the plant-name exemptions."""
    return bool(fire_words(text))


def out_of_script_characters(text: str) -> list[str]:
    """Every letter, digit or other symbol of the text beyond Latin-1."""
    return [
        character
        for character in text
        if ord(character) > LATIN_1_LAST and unicodedata.category(character) in OUT_OF_SCRIPT_CATEGORIES
    ]


def fire_tokens(text: str, allowed: FireTextAllowList) -> list[str]:
    """Every out-of-script character, and every fire-stem word left in a segment once the allowed texts are removed."""
    found: list[str] = []
    for reading in normalised_readings(text):
        found.extend(out_of_script_characters(reading))
        for segment in segments(allowed.without_allowed(reading)):
            found.extend(fire_words(segment))
    return found


def metadata_texts(metadata: Mapping[str, str]) -> Iterator[str]:
    """Every string in the metadata values, JSON values decoded and walked (keys too), never scanned as JSON text."""

    def walk(value: object) -> Iterator[str]:
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for key, item in value.items():
                yield str(key)
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)

    for text in metadata.values():
        try:
            decoded = json.loads(text)
        except ValueError:
            decoded = text
        yield from walk(decoded)


def served_texts(cells: pl.DataFrame) -> list[str | None]:
    """Every value of every string and list-of-string column."""
    texts: list[str | None] = []
    for name, dtype in cells.schema.items():
        if dtype == pl.String:
            texts.extend(cells[name].to_list())
        elif dtype == pl.List(pl.String):
            texts.extend(cells.select(pl.col(name).explode(empty_as_null=True)).to_series().to_list())
    return texts


def assert_no_fire_claims(
    cells: pl.DataFrame, metadata: Mapping[str, str] | None = None, allowed: FireTextAllowList | None = None
) -> None:
    """Raise when a served value or decoded metadata string has a fire word off the allow-list or a non-Latin letter."""
    allow_list = served_allow_list(()) if allowed is None else allowed
    texts = dict.fromkeys([*served_texts(cells), *metadata_texts(metadata or {})])
    offending = {text: tokens for text in texts if text and (tokens := fire_tokens(text, allow_list))}
    if offending:
        text, tokens = next(iter(offending.items()))
        message = (
            f"{len(offending)} served texts carry fire words outside the allow-list (or characters beyond Latin-1), "
            f"e.g. {tokens} in {text!r}; a real plant name is fixed by adding it to labels.PLANT_NAME_EXEMPTIONS, "
            "never by loosening FIRE_STEM_PATTERN"
        )
        raise ValueError(message)


def fire_wording(texts: Iterable[str]) -> list[str]:
    """Every text that claims a fire effect, or holds a fire word or character egress refuses (fixed allow-list)."""
    candidates = list(texts)
    claimed = set(find_fire_claims(candidates))
    allow_list = served_allow_list(())
    return [text for text in candidates if text in claimed or fire_tokens(text, allow_list)]


def carries_fire_field(text: str) -> bool:
    """True when the text holds the PLANTS fire field: the label prefix or any 'Fire Resistant' wording."""
    return any(FIRE_FIELD_PATTERN.search(reading) for reading in normalised_readings(text))


def assert_no_fire_field_on_woody_buffer(labels_by_guild: Mapping[str, Iterable[str | None]]) -> None:
    """Raise when a woody-buffer text carries the fire field, or another guild's label lacks the verbatim one."""
    for guild, labels in labels_by_guild.items():
        texts = [label for label in labels if label]
        if guild in GUILDS_WITH_FIRE_LABEL:
            offending = [label for label in texts if not label.startswith(FIRE_LABEL_PREFIX)]
            problem = "lack the verbatim PLANTS fire field"
        else:
            offending = [label for label in texts if carries_fire_field(label)]
            problem = "carry the PLANTS fire field (the woody buffer carries none)"
        if offending:
            message = f"{len(offending)} {guild} labels {problem}, e.g. {offending[0]!r}"
            raise ValueError(message)
