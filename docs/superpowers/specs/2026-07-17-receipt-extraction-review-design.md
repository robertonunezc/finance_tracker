# Receipt Extraction Review Design

## Goal

Add observability, validation, confidence scoring, and manual review for receipt LLM extractions before they are trusted by reports.

The first version is scoped to the current receipt pipeline. It should use patterns that can later be copied by future bank statement or audio extraction flows, but it should not introduce a generic extraction framework before those flows exist.

## Scope

- Validate every receipt extraction before it is marked `completed`.
- Capture raw LLM extraction values, source evidence, field confidence, derived confidence, and validation issues.
- Mark receipts that fail blocking rules as `needs_review`.
- Exclude `needs_review` receipts from reports by keeping the existing reports filtered to `completed`.
- Add a staff-only review queue and review detail page.
- Let reviewers correct receipt-level and item-level values.
- Block approval until validation rules pass after correction.
- Preserve the original LLM extraction separately from corrected values.

## Non Goals

- Build validation for bank statements in this iteration.
- Build a generic per-field issue workflow or assignment system.
- Include low-confidence receipts in spending reports before review.
- Add force approval in the first version.
- Replace the existing Telegram upload flow beyond the extraction completion handoff.

## Chosen Approach

Use a dedicated receipt review record with structured JSON issues.

The existing `Receipt.extraction_result` field will continue to store broad extraction/audit data. A new `ReceiptExtractionReview` model will provide the queryable review queue, current issue state, confidence score, and approval metadata.

This avoids overloading `Receipt.extraction_result` for workflow state while also avoiding a full issue-tracking schema for every individual field in the first version.

## Alternatives Considered

### JSON-only audit on `Receipt.extraction_result`

This would reuse the existing field and require fewer migrations. It is fast to implement, but it makes the review queue and issue filtering awkward because status, confidence, and issue count would be buried inside a JSON blob.

### Full per-field issue models

This would add a separate row for every field or item issue. It offers maximum workflow control, but it is too heavy for the first version and would make the review UI more fragmented than needed.

## Receipt Lifecycle

Add a new valid receipt status:

- `needs_review`

The lifecycle becomes:

1. Telegram upload creates a receipt with `pending`.
2. Celery marks the receipt `processing`.
3. The LLM returns a structured extraction.
4. The validation service evaluates the extraction and enriched item categories.
5. If all blocking rules pass:
   - save receipt and item values
   - persist extraction audit data
   - mark receipt `completed`
6. If any blocking rule fails:
   - save receipt and item values as current draft values
   - create or update the review record
   - mark receipt `needs_review`
7. A staff reviewer saves corrections from the review detail page.
8. Approval reruns validation.
9. If validation passes, mark the review `approved` and receipt `completed`.
10. If validation still fails, keep the receipt `needs_review` and show the remaining issues.

Duplicate upload behavior should treat `needs_review` as an existing actionable receipt. It should not enqueue a second extraction for the same file.

## Data Model

Add `needs_review` to `STATUS_CHOICES`.

Add `ReceiptExtractionReview` in the `receipt` app:

- `receipt`: one-to-one relationship with `Receipt`
- `status`: `needs_review` or `approved`
- `overall_confidence`: float or decimal between `0` and `1`
- `issues`: JSON list of structured validation issues
- `raw_extraction`: JSON snapshot of the original LLM extraction, including source evidence and LLM confidence
- `corrected_payload`: JSON snapshot of the latest reviewer corrections, nullable
- `approved_by`: staff user identifier, nullable
- `approved_at`: nullable timestamp
- `created_at`: timestamp
- `updated_at`: timestamp

The app does not currently use Django users for end-user receipts. For review approval, `approved_by` can be a string derived from the authenticated staff user until a stronger user model exists.

## Extraction Contract

The receipt LLM response should include normalized values plus source evidence and LLM confidence for receipt-level and item-level fields.

Conceptual shape:

```json
{
  "store_name": {
    "value": "amazon",
    "source_text": "AMZN MX MARKETPLACE",
    "confidence": 0.72
  },
  "total": {
    "value": 1249.00,
    "source_text": "AMZN MX MARKETPLACE  1,249.00",
    "confidence": 0.68
  },
  "items": [
    {
      "name": {
        "value": "AMZN MX MARKETPLACE",
        "source_text": "AMZN MX MARKETPLACE  1,249.00",
        "confidence": 0.80
      },
      "price": {
        "value": 1249.00,
        "source_text": "AMZN MX MARKETPLACE  1,249.00",
        "confidence": 0.62
      },
      "quantity": {
        "value": 1,
        "source_text": "AMZN MX MARKETPLACE  1,249.00",
        "confidence": 0.75
      },
      "category": {
        "value": "electronics",
        "source_text": "AMZN MX MARKETPLACE",
        "confidence": 0.70
      }
    }
  ]
}
```

The implementation can adapt exact Pydantic class names, but each validated field should preserve:

- normalized value
- raw source text or source snippet used as evidence
- LLM-provided confidence from `0` to `1`

## Validation And Confidence

Use a hybrid confidence approach:

- Accept LLM field confidence as one input.
- Apply deterministic rules that can reduce confidence or force review.
- Store the resulting `overall_confidence` on the review record.

Initial confidence threshold:

- Any relevant receipt or item field below `0.80` creates a blocking issue.

Initial blocking rules:

- `low_confidence`: receipt or item field confidence is below `0.80`.
- `item_sum_mismatch`: sum of `price * quantity` differs from extracted total by more than the configured tolerance.
- `source_amount_mismatch`: extracted numeric amount differs from the amount parsed from the field source text.
- `missing_required_value`: missing total, item name, item price, or invalid category.

Initial tolerances:

- Source amount mismatch should be exact after parsing locale formats such as `1,249.00`.
- Item sum vs total can allow a small tolerance, starting at `1.00 MXN`, to avoid blocking on rounding or receipt oddities.

Validation output should include:

- `overall_confidence`
- `requires_review`
- `issues`

Issue shape:

```json
{
  "path": "items[0].price",
  "code": "source_amount_mismatch",
  "severity": "blocking",
  "message": "Extracted amount differs from source evidence.",
  "extracted_value": "12490.00",
  "source_text": "AMZN MX MARKETPLACE  1,249.00"
}
```

## Service Boundaries

Add a receipt extraction review service. Exact file placement can be chosen during implementation, but the boundary should stay inside the `receipt` app because it owns receipt state and persistence.

Primary responsibilities:

- Convert the LLM result into an audit payload.
- Validate extraction payloads and corrected receipt values.
- Save receipt and item values after extraction.
- Create or update the review record.
- Save reviewer corrections.
- Approve a reviewed receipt only when blocking issues are resolved.

Candidate APIs:

- `validate_receipt_extraction(payload) -> ValidationResult`
- `apply_extraction_result(receipt_id, ticket) -> ReceiptExtractionReview | None`
- `save_review_corrections(receipt_id, form_data) -> ValidationResult`
- `approve_review(receipt_id, user) -> ValidationResult`

`extract_info.tasks.process_file_task` should stop owning completion logic directly. After extraction and category enrichment, it should call the receipt review/application service, then send a Telegram message that reflects either `completed` or `needs_review`.

## Review UI

Add a staff-only server-rendered review area.

Recommended routes:

- `/review/receipts/`: review queue
- `/review/receipts/<receipt_id>/`: review detail and correction form

The queue should show receipts with `status=needs_review`, including:

- store name
- total
- item count
- overall confidence
- issue count
- created or updated age

The detail page should show:

- receipt image URL when renderable
- source evidence snippets from the review record
- overall confidence
- blocking issues
- editable receipt fields: store name, total, subtotal, discount
- editable item rows: name, quantity, price, category
- issue badges near affected fields
- `Save corrections`
- `Approve`

`Save corrections` updates current receipt and item values, stores a corrected snapshot, reruns validation, and keeps the receipt in `needs_review`.

`Approve` reruns validation and only succeeds if no blocking issues remain. If approval is blocked, the page shows remaining issues near the affected fields.

The UI should use Django templates and Bootstrap, matching the existing reports app style. No SPA is needed.

## Authentication

The review queue and detail page must require Django staff authentication.

The app currently has public report pages and Telegram allow-list authentication. Review is different because it edits financial records. The first implementation should require staff users through Django auth decorators or equivalent access checks.

## Error Handling

- Extraction or parsing exceptions still mark the receipt `failed`.
- Validation exceptions should be logged.
- If enough extraction data exists but validation itself fails unexpectedly, prefer `needs_review` over `completed`.
- Approval must be blocked while any blocking issue remains.
- The first version does not include force approval.

## Reporting Behavior

Existing reports should continue to filter on `Receipt.status == "completed"`.

Because `needs_review` receipts are not completed, they remain out of:

- category spending totals
- receipt item reports
- future trusted financial summaries that follow the same completed-only pattern

## Testing

Add focused tests for the service and workflow:

- low-confidence field creates `needs_review`
- source amount mismatch creates `needs_review`
- item sum mismatch creates `needs_review`
- missing required values create `needs_review`
- valid extraction marks receipt `completed`
- extraction audit data is preserved separately from corrected values
- review correction can move a receipt from `needs_review` to `completed`
- approval is blocked while issues remain
- duplicate upload treats `needs_review` as an existing actionable receipt
- reports continue to exclude `needs_review`

UI tests can stay light in the first version. The main risk is data quality and state transitions, so service tests are the priority.

## Verification

Verification should include:

- running focused receipt extraction review service tests
- running duplicate receipt tests after adding `needs_review` handling
- running report tests or smoke checks to confirm `needs_review` stays excluded
- running the full Django test suite if the local database is available
- manually smoke testing the staff review queue and detail form
