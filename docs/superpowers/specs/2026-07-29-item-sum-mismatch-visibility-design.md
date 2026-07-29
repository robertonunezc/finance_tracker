# Item Sum Mismatch Visibility Design

## Context

When a receipt enters manual review with `total · item_sum_mismatch`, the review page only shows the issue code and a generic message. The reviewer cannot see which numbers were compared without manually adding item line totals and comparing them to the receipt total.

The mismatch is produced in `receipt/extraction_review.py` by `_validate_item_sum()`. The issue is attached to the `total` path and rendered in `receipt/templates/receipt/review_detail.html` in two places:

- the left-side Blocking issues panel
- the inline issue badges below the Total input

## Goal

Show the exact values compared for `item_sum_mismatch` during ticket review, in both places where the issue appears:

- receipt total
- sum of item line totals
- difference
- configured tolerance

## Non-Goals

- Do not change mismatch validation rules.
- Do not change the review approval workflow.
- Do not add a new database field or migration.
- Do not recalculate a separate mismatch summary only in the view.

## Recommended Approach

Enrich the existing `item_sum_mismatch` issue dictionary with a small `details` payload from the validator, then render that payload generically in the review template when present.

Example issue detail shape:

```json
{
  "path": "total",
  "code": "item_sum_mismatch",
  "severity": "blocking",
  "message": "Receipt total differs from the sum of item line totals.",
  "extracted_value": "1300.00",
  "source_text": "TOTAL 1300.00",
  "details": {
    "receipt_total": "1300.00",
    "item_line_total_sum": "1249.00",
    "difference": "51.00",
    "tolerance": "1.00"
  }
}
```

This keeps the displayed comparison tied to the same validation pass that created the issue.

## Alternatives Considered

Recompute the comparison in the view. This would be smaller at first, but it duplicates validation logic and can drift from the validator. It also makes saved historical issue records less self-explanatory.

Add a top-level review summary field. This would be useful if many issue types needed rich summaries, but it adds structure and persistence surface that this narrow visibility problem does not need.

## Data Flow

1. `_validate_item_sum()` computes `total`, `items_total`, and `difference`.
2. When the difference exceeds `ITEM_TOTAL_TOLERANCE`, it appends an `item_sum_mismatch` issue with `details`.
3. `ReceiptExtractionReview.issues` stores the enriched issue JSON.
4. `receipt.views.review_detail()` continues grouping issues by `path`; no special view-only recomputation is needed.
5. The review detail template renders issue details in:
   - the Blocking issues list
   - the inline Total field issue area

## UI Behavior

In the Blocking issues panel, the issue should still show:

- `total · item_sum_mismatch`
- the existing message
- source text, when available

When `details` exists, it should also show a compact comparison block:

- Total: `$1300.00`
- Item lines: `$1249.00`
- Difference: `$51.00`
- Tolerance: `$1.00`

Under the Total field, the existing badge should remain. For issues with `details`, the same comparison block should appear directly below the badge list so the reviewer can see it while editing the total.

## Formatting

The validator should store decimal strings quantized to two places. The template can prefix these values with `$` because the application already presents review amounts as currency values.

The comparison block should use small, readable text and fit inside the existing review card layout without introducing a new page section.

## Testing

Add focused tests in `receipt/tests.py`:

- `ReceiptExtractionValidationTests.test_item_sum_mismatch_includes_compared_values`
  - create a mismatch payload
  - assert the issue has `details.receipt_total`, `details.item_line_total_sum`, `details.difference`, and `details.tolerance`

- `ReceiptReviewViewTests.test_detail_renders_item_sum_mismatch_compared_values`
  - create a review receipt with a total/item sum mismatch
  - load the detail page as staff
  - assert the comparison values render in the Blocking issues panel
  - assert the comparison values render inline under the Total field

Run the targeted receipt tests after implementation.
