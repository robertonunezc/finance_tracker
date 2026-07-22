# LLM Extraction Evaluation — Learning Roadmap

A hands-on roadmap for evaluating the receipt-extraction pipeline, learned by
porting patterns from Anzen's `analysis-api` eval system onto **this** codebase.

Each pattern is: **what problem it solves** → **how it maps to our code** →
**a concrete exercise with a "done when."** Work them one at a time.

> **Core idea to internalize:** you cannot "check" an LLM output for correctness
> in isolation — there is no oracle in the program that knows the right answer.
> So trust comes from two separate places:
> 1. **Per-response trust** = making each output *verifiable* (grounding + reasoning + reconciliation).
> 2. **Aggregate correctness** = *measuring* how often the model is right against a **human-labeled golden dataset**.
>
> We have already built #1. We have not built #2 — even though we are already
> *collecting* the golden data on every approved review.

---

## The three sub-problems (keep them separate everywhere)

A receipt is not one extraction problem, it's three — and each is evaluated differently.

| Sub-problem | Example | Eval shape |
|---|---|---|
| **Scalar fields** | `store_name`, `total`, `subtotal`, `discount` | field **accuracy** |
| **Line-item set** | list of `(name, qty, price)` rows | **set** precision / recall / F1 |
| **Category** | "Bananas" → `produce` (pgvector step) | classification **accuracy** |

Do not collapse these into one number — one blended accuracy hides which stage is failing.

---

## Scorecard — where we stand today

### Already built ✅

| # | Pattern | Where in our code |
|---|---|---|
| 2 | **Structured output + nullable values** | `Ticket`/`Item`/`*ExtractionField` in `extract_info/services.py:38-67`. `value: Optional[...]` lets the model say "absent" instead of hallucinating. |
| 3 | **Grounding over confidence** | Every field carries `source_text` (`services.py:40-53`); `_validate_source_amount` (`receipt/extraction_review.py:350-376`) checks the extracted amount actually appears in `source_text`. This is *verifiable* grounding — the strongest pattern in the repo. |
| 6 | **Domain reconciliation** | `_validate_item_sum` (`extraction_review.py:379-403`): `sum(price*qty)` vs `total`, ±`ITEM_TOTAL_TOLERANCE` (1.00). Plus required-total, items-present, quantity, category checks. |
| 7 | **Golden-data collection** (data only) | `ReceiptExtractionReview.raw_extraction` (LLM) vs `corrected_payload` (human), written in `_apply_review_action` (`extraction_review.py:575-620`). **A ready-made golden dataset we are not yet evaluating against.** |

### Missing ❌

| # | Pattern | Note |
|---|---|---|
| 1 | **Determinism** | `chat.completions.parse` (`services.py:110-148`) sets no `temperature`/`seed` → non-reproducible outputs → noisy eval. 5-min fix. |
| 4 | **Reasoning field** | We have `source_text` (evidence) but no `reasoning` (rationale). Minor. |
| 5 | **Targeted retry** | No retry on `ValidationError`/parse failure. |
| 8 | **Real-path `predict()`** | No eval harness exists. |
| 9 | **Observations + missed=None** | The recall trick — the heart of the harness. |
| 10 | **Scoring cascade** | Pieces exist (amount tolerance, normalization) but not assembled for scoring. |
| 11 | **LLM-as-judge** | For product-name semantic equivalence. |
| 12 | **P/R/F1 segmented** | No metrics computed. |
| 14 | **Rubric-as-config** | Thresholds hardcoded (`CONFIDENCE_THRESHOLD`, `ITEM_TOTAL_TOLERANCE`). |
| 15–16 | **Regression diff / cost** | No per-run tracking. |
| 17 | **Separate predict/score** | `extraction_review` is close; scoring should not import extraction logic. |

---

## The insight that matters most for us: confidence calibration

We gate `needs_review` on `overall_confidence = min(field confidences)` from the LLM
(`extraction_review.py:75-80`), threshold `0.80`. **We have never tested whether that
number predicts real errors.** This is the classic "LLM self-reported confidence is
poorly calibrated" trap.

The golden data lets us settle it: for each field we know the LLM's self-reported
confidence (`raw_extraction`) *and* whether it was actually correct (did the human
change it in `corrected_payload`?). So we can measure:

> "When the LLM said 0.9, how often was it actually right?"

If 0.9-confidence fields are right only ~70% of the time, the gate is misleading and
our grounding checks (which we already have) are the better trust signal. This is a
**measurable** conclusion, and it falls out of the harness below for free.

---

## Roadmap order

1. **Pattern 1 — Determinism** (quick win)
2. **Pattern 5 — Targeted retry** (quick win)
3. **Patterns 7 → 8 → 9 → 12 — The eval harness** (the big one)
4. **Patterns 10 → 11 — Scoring cascade + LLM judge**
5. **Pattern 14 — Rubric-as-config**
6. **Patterns 15 → 16 — Regression diff + cost tracking**
7. **Patterns 4 / 17 — Reasoning field + predict/score separation** (polish)

---

## Phase 0 — Quick wins

### Pattern 1 — Determinism for reproducibility
- **Problem:** If the same receipt yields different answers per run, the eval score is
  noise and you can't attribute changes to your work.
- **Apply:** Add `temperature=0, seed=0` to the `parse` call in `services.py:110-148`
  (and the audio path ~`220-233`).
- **Done when:** the same receipt returns byte-identical structured output twice. If
  vision variance remains, record it — that's your measurement noise floor.

### Pattern 5 — Targeted retry
- **Problem:** LLMs occasionally emit malformed/incomplete output; retry only on
  *recoverable* errors, not everything (light-touch error handling).
- **Apply:** Wrap the extraction call to retry ≤3× only on Pydantic `ValidationError` /
  JSON parse failure. Do not retry on refusals or 4xx.
- **Done when:** a forced schema violation retries then fails cleanly; a refusal does not retry.

---

## Phase 1 — The eval harness (Patterns 7 → 8 → 9 → 12)

Build this as a **new Django management command** (e.g.
`receipt/management/commands/evaluate.py`). It changes nothing in the live pipeline.

### Step A — Golden loader (Pattern 7)
- **Problem:** No oracle exists; the only ground truth is human-verified answers.
- **Apply:** Query `ReceiptExtractionReview.objects.filter(status="approved")`. Each row
  gives a pair: `raw_extraction` (prediction) and `corrected_payload` (truth).
- **Done when:** you can load the pairs and print `len(golden)`.

### Step B — Real-path predict (Pattern 8) *(optional for v1)*
- **Problem:** If the eval reimplements extraction, you measure a fiction.
- **Apply:** For a "live" mode, `predict(receipt)` must call the real
  `extract_info.services.extract_receipt_text`. For v1 you can score the *stored*
  `raw_extraction` directly (cheaper, no API calls); add live mode later.
- **Done when:** no extraction logic is duplicated in the eval.

### Step C — Observations with the missed-item trick (Pattern 9)
- **Problem:** Scoring only "of what I extracted, how much was right?" measures
  **precision** and silently ignores **recall** (missed items). Receipts vary in item
  count, so recall is where failures hide.
- **Apply:** Flatten each receipt into scorable observations, per sub-problem:
  - **Scalars** (`store_name`, `total`, `subtotal`, `discount`): one observation each.
  - **Line items:** match predicted rows to golden rows on a key like
    `(normalized_name, price)`.
    - golden row NOT predicted → emit `value=None` (a **miss** → recall/false negative)
    - predicted row NOT in golden → **false positive** → precision
  - **Categories:** predicted vs corrected category per matched item.
- **Done when:** a receipt where the model dropped 1 of 8 items produces 8 line-item
  observations, the dropped one as `value=None`.

### Step D — Metrics, segmented (Pattern 12)
- **Problem:** One accuracy number hides which stage is failing.
- **Apply:** Compute and print separately:
  ```
  scalar_accuracy:   total=?.??  store=?.??  subtotal=?.??  discount=?.??
  line_items:        precision=?.??  recall=?.??  f1=?.??
  category_accuracy: ?.??
  ```
  Micro-F1: `f1 = 2*tp / (2*tp + fp + fn)`.
- **Done when:** you can point at the lowest number and know which stage to fix.

### Bonus — Confidence calibration check
- Bucket every scalar/item field by its LLM confidence (`[0,0.5)`, `[0.5,0.8)`,
  `[0.8,1.0]`) and print actual accuracy per bucket. Directly tests the `0.80` gate.

---

## Phase 2 — Scoring quality (Patterns 10 → 11)

### Pattern 10 — Scoring cascade (cheap+strict → expensive+fuzzy)
- **Problem:** `"Milk 2%"` vs `"2% MILK"` are the same answer, different string.
  Exact match is too brittle; LLM-judging everything is too slow/costly.
- **Apply:** Per observation, in order:
  1. Exact match after normalizing case/whitespace (free).
  2. Missing-value-as-wrong: predicted empty but golden has a value → wrong (this is
     what penalizes the `None` observations from Step C).
  3. Numeric tolerance for money (`abs(a-b) <= 0.01`) — reuse
     `parse_amounts_from_source_text` logic from `extraction_review.py:45-57`.
  4. LLM-as-judge (Pattern 11) for survivors only.
- **Done when:** `"Milk 2%"` vs `"2% MILK"` still counts as a mismatch under steps 1–3
  (so you *feel* why step 4 is needed).

### Pattern 11 — LLM-as-judge
- **Problem:** Semantic equivalence exact rules can't capture (mostly product names).
- **Apply:** Cheap model (e.g. `gpt-4o-mini`) graded against *human-confirmed* answers.
  Force reasoning-before-verdict output: `{thoughts: str, matches: bool}`. Prompt it to
  be strict: reordering/rewording OK, meaning change = not a match.
- **Done when:** the pair above scores as a match and `thoughts` explains why.
  Spot-check ~10 verdicts against your own judgment — that's calibrating the judge.

---

## Phase 3 — Operationalize (Patterns 14 → 15 → 16)

### Pattern 14 — Rubric-as-config
- **Problem:** If *how* you score changes between runs, the numbers aren't comparable.
- **Apply:** Move scoring config (tolerances, which fields in which bucket, judge model)
  into a versioned `eval_config.yaml`; hash it so two runs are only compared when identical.
- **Done when:** changing scoring is a visible config diff, not a code edit.

### Pattern 15 — Regression comparison
- **Problem:** "Did my prompt change actually help?" — the question the whole system exists to answer.
- **Apply:** Store each run's metrics with the git commit / prompt version; compare
  branch vs. main on the same golden set + same config.
- **Done when:** you accept/reject a prompt change on the number, e.g.
  `line_recall 0.68 → 0.77 (+0.09)`.

### Pattern 16 — Cost & latency tracking
- **Problem:** Accuracy isn't free.
- **Apply:** Record tokens / cost / latency per receipt in the eval run.
- **Done when:** you can say "prompt B is +4% F1 but 2× cost" and choose deliberately.

---

## Phase 4 — Polish (Patterns 4, 17)

- **Pattern 4 — Reasoning field:** add `reasoning: str` alongside `value` in the
  `*ExtractionField` models (`services.py:38-53`), filled *before* the value. Improves
  debuggability (and mildly, accuracy).
- **Pattern 17 — Separate predict/score:** keep scoring methods + metrics in a module
  that imports nothing from `extract_info.services`. Swappable extractor, stable scorer.

---

## Quick reference — the whole map

```
Phase 0  quick wins   → 1 determinism · 5 targeted retry
Phase 1  harness      → 7 golden loader · 8 real-path predict · 9 observations+missed=None · 12 P/R/F1 segmented
Phase 2  scoring      → 10 scoring cascade · 11 LLM-as-judge
Phase 3  operate      → 14 rubric-as-config · 15 regression diff · 16 cost/latency
Phase 4  polish       → 4 reasoning field · 17 separate predict/score

Already have ✅       → 2 structured+nullable · 3 grounding(source_text) · 6 reconciliation · 7 golden-data collection
```

**Start here:** Patterns **7 + 9 + 12** (golden loader → observations with missed items
→ segmented P/R/F1). Smallest slice that produces a real number, and it exploits the
gold already collected in the review flow. Optionally do the two quick wins (1, 5) first.

---

## Key file references (this repo)

- LLM extraction call + prompt + Pydantic schema: `extract_info/services.py:18-148`
- Embeddings + pgvector category lookup: `extract_info/services.py:151-268`,
  `receipt/services.py:302-305` (`CosineDistance`)
- Async orchestration (category enrich, then apply): `extract_info/tasks.py:52-86`
- Validation / hard rules / reconciliation: `receipt/extraction_review.py` (item-sum
  `379-403`, source-amount `350-376`, confidence `281-347`, orchestrator `60-81`)
- Django models: `receipt/models.py` (`Receipt` `60-93`, `ReceiptItem` `96-106`,
  `ReceiptExtractionReview` `109-135`)
- **Golden pair persistence:** `receipt/extraction_review.py:575-620`
  (`raw_extraction` vs `corrected_payload`)
- Correction UI: `receipt/views.py:115-161`,
  `receipt/templates/receipt/review_detail.html`
- Existing validation tests (good patterns to mirror): `receipt/tests.py:422-929`

## Reference — the source system

Patterns adapted from Anzen `analysis-api` (`packages/analysis-api/analysis_api/labelling/`)
and its scoring service `labelling-v2`. Notable analogues:
- Observations + missed=None: `labelling/tasks/email_info_extraction.py:38-103`
- LLM-as-judge: `labelling-v2/labelling_v2/scoring_methods.py:369-523`
- Set precision/recall/F1: `SetMatchPrecisionRecall` + `AggregateF1Metric` in `labelling-v2`
- Rubric templates: `labelling-v2/resources/templates/insurance_doc_extraction.yaml`
- Determinism: `analysis_api/services/info_extraction/core.py:63` (`temperature=0, seed=0`)
