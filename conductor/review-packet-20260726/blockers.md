---
type: decision-record
---

# Current model blockers and choices

## Crop-spectrum benchmark: insufficient independent rice groups

**Observed:** the receipt-bound GHISACONUS CSV has 6,988 complete historical
signatures across 99 source images. The intended primary target is five-class
`Crop`; its primary feature set is the 131 spectral bands; `Image` is the
required leakage-control group. Rice appears in only **two** independent images.

**Why it matters:** a train/validation/final test split needs at least three
independent images per class. A random row split would put near-duplicate pixels
from the same image on both sides of an evaluation and make the result look more
useful than it is. Removing rice or changing the target is a product and
scientific decision, not an implementation workaround.

**What is not broken:** source hashing, row completeness, band completeness,
and the historical-crop target are intact. Two exact duplicate spectral vectors
were found but are retained; their proposed exclusion applies only to identical
copies from the same image and crop. A cross-image or cross-label duplicate
would stop the lane.

| Next option | Result | Trade-off |
| --- | --- | --- |
| Acquire another independent rice image | Preserves the five-class benchmark | Requires a new retained/reviewed release. |
| Define a four-class historical benchmark | May permit a valid grouped benchmark | Must visibly say that rice is unsupported. |
| Build an evidence explorer first | Shows spectra, source coverage, and the gap | Delivers no classifier, but is demonstrable now. |

## Weather backtest: unavailable at simulated forecast origins

**Observed:** the frozen export is byte-for-byte valid: 1,462 daily NASA POWER
observations and 98 scored outcomes from 14 seven-day origins. Every source row
was recorded on 2026-07-21, after each simulated origin. The values are valid
hindsight context, not values known at forecast time.

**Why it matters:** using those values during an older simulated origin leaks
future information. A persistence/seasonal/ridge comparison would then be a
retrospective curve fit, not a forecast evaluation.

**Additional scope limit:** this is one Denver NASA POWER point with a declared
55,660-m support. It is neither field-level nor regional coverage, and 14
seven-day origins cannot justify 30-day seasonal model selection.

| Next option | Result | Trade-off |
| --- | --- | --- |
| Retain historical releases with origin-time availability | Enables a time-honest seven-day benchmark | Requires source/version/availability capture before fitting. |
| Run a labelled hindsight reconstruction | Useful for feature/method research | Cannot be called a forecast score or select a product. |
| Build a weather evidence card first | Demonstrates support, freshness, and abstention | No predictive score until availability history exists. |

## Product scope: target data is not yet present

The warehouse has a valuable shared evidence plane, but not validated targets
for water use, energy, field vegetation, soil, yield/cost, biodiversity,
scenario outcomes, or restoration interventions. Those are distinct models, not
one generic strategy model. A goal starts after its target, source/release,
native support, availability time, and holdout method are agreed and retained.

## Recommended next working session

Start with the crop choice: acquire another rice image, transparently switch to
four supported crop classes, or launch an evidence explorer. In parallel, choose
whether weather is a hindsight research spike or whether to capture
availability-aware historical releases first.
