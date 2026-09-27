# Brief: chunk a source for the strategy-knowledge corpus

You are hand-chunking source text for a retrieval knowledge base of environmental support and
enrichment strategies (soil, water, carbon, erosion, wildfire resilience, biodiversity, nutrients,
remediation, biomass circularity, drought adaptation). An AI agent will search your chunks,
findings and strategies to choose land-management strategies. Precision beats volume.

## Read first (in the working directory)
1. `DESIGN.md` - the frozen contract. Sections 3-5 define every field you emit. Follow it exactly.
2. `strategy_schema.md` - closed enums (category, land_use, region, soil_conditions, scale, level,
   evidence_strength, source_type) and the StrategyRecord shape used by candidate strategies.
3. `briefs/strategy_ids.txt` - the existing strategy ids for `linked_strategy_ids` and `matches_existing`.

## How to work
- Read your assigned line ranges with the Read tool (offset/limit, ~250-400 lines per call). The
  line numbers Read prints ARE the line numbers you record. Never estimate a line number.
- Walk the text in order. Decide chunk boundaries at real structure: headings, sub-sections,
  numbered procedures, tables, a single research-abstract entry. One coherent idea per chunk.
  Target 120-400 words; never split a table or a numbered procedure; fold fragments < 60 words into
  a neighbour.
- Every assigned line lands in exactly ONE chunk or ONE skipped range. Prefer a `noise`/`references`
  chunk over a skip when the text has any retrieval value. Skip only: the `# ...` file header plus the
  blank line after it (usually lines 1-6 - check), and administrative boilerplate with zero retrieval
  value (author contributions, funding, conflict-of-interest, publisher's notes).
- PDF sidebars and captions: a **headed sidebar or box of 60+ words** is its own chunk (section_path
  ends with "<title> (sidebar)"), even when it physically interrupts a body paragraph - fold the
  interrupted paragraph's two halves into the neighbouring chunks. Figure/photo captions, running page
  headers and bare page numbers are furniture: fold them into whichever chunk encloses them.
- Output **line ranges and metadata only - never copy the chunk text**. A script slices the verbatim
  text later.
- For each chunk, extract any real **findings** it contains (DESIGN.md section 5 Finding): measured,
  reviewed or quantified results. `excerpt` is copied VERBATIM (8-25 words) from one uninterrupted run
  of prose - avoid spans broken by figure captions, page headers or drop-cap letters, because a script
  checks it character-for-character (whitespace-insensitive). PDF traps: justified text breaks words
  across lines ("sedi-" / "ment", or "signifi" / "cant" with no hyphen) and splits ratios ("C:" / "N")
  - choose spans that avoid a line-wrapped word, or copy the hyphen exactly as the raw text has it.
- `magnitude` holds ONLY the verbatim number(s) with units as printed ("30–60%", "1-2 tons/acre",
  "84–90 Mg/ha") - no paraphrased words around them. Put context in `conditions`.
- One candidate per `matches_existing` id per slice: if several passages support the same existing
  strategy, merge their citations and rates into that single candidate.
- Candidate scope: hands-on field practices, and decision/monitoring tools a land manager adopts as a
  practice (category `monitoring_assessment`). Industrial or lab processes that never touch the land
  (e.g. a water-treatment reactor) are findings, not candidates.
- Emit **candidate_strategies** for actionable interventions (DESIGN.md section 5 CandidateStrategy).
  If the intervention is already in `briefs/strategy_ids.txt`, set `matches_existing` and include
  only what is new (citations, rates, conditions). Citations need verbatim excerpts too.
- `goals`: "stated" only when the text claims that outcome; "inferred" when it is a reasonable
  application the text does not claim. Omit goals that do not apply.
- `keywords`: exact strings a keyword search must hit - species, chemicals, products, equipment,
  places, rates with units. 3-10 per chunk; none for noise.
- Paraphrase every summary/claim. Never reproduce more than an excerpt's 25 words from the source.

## Output
Write ONE file: `chunked/<slice_id>.json`, shaped exactly as DESIGN.md section 5. Valid JSON: no
comments, no trailing commas. Large outputs: you MAY assemble the JSON in a Python script
(json.dump, ensure_ascii=False) and self-check coverage and excerpts there before writing - but do
NOT run `validate_chunked.py` (the orchestrator runs it at the join). For a source that already has a
SourceRecord in `extracted/*.json`, omit `source_record`; otherwise include one (with `goals`).
Region enum (v1.1) now also has us_midwest, us_southeast, us_northeast, alaska, canada,
latin_america, europe, asia, africa, oceania - see strategy_schema.md.

## Rules
- Do NOT run tests, builds, linters, or the validator. Do NOT browse the web. Do NOT touch any file
  other than your own output file.
- Your final message (under 200 words): chunks / findings / candidate strategies per source, any
  line range you could not classify, and anything in DESIGN.md that did not fit this source (do not
  work around the contract silently - report it).
