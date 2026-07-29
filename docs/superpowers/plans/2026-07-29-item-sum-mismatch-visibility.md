# Item Sum Mismatch Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the compared values behind `total · item_sum_mismatch` in both the Blocking issues panel and the inline Total field issue area.

**Architecture:** Enrich the existing validator-produced issue dictionary with a `details` payload so persisted review issues explain the comparison that triggered review. Render those details through the existing review-detail template without adding a database field, migration, or view-only recomputation.

**Tech Stack:** Django 6.0, Django templates, Django `TestCase`, Python `Decimal`.

## Global Constraints

- Do not change mismatch validation rules.
- Do not change the review approval workflow.
- Do not add a new database field or migration.
- Do not recalculate a separate mismatch summary only in the view.
- Render compared values in both the Blocking issues panel and under the Total field.
- Store comparison decimals as two-place strings.

---

### Task 1: Persist Compared Values On Item Sum Mismatch Issues

**Files:**
- Modify: `receipt/extraction_review.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Consumes: `validate_receipt_extraction(payload: Mapping[str, Any]) -> ValidationResult`
- Produces: `item_sum_mismatch` issue dictionaries with `details: dict[str, str]`
- Produces details keys: `receipt_total`, `item_line_total_sum`, `difference`, `tolerance`

- [ ] **Step 1: Write the failing validator test**

Add this test to `ReceiptExtractionValidationTests`:

```python
def test_item_sum_mismatch_includes_compared_values(self):
    from receipt.extraction_review import validate_receipt_extraction

    payload = self.valid_payload()
    payload["total"]["value"] = "1300.00"
    payload["total"]["source_text"] = "TOTAL 1300.00"

    result = validate_receipt_extraction(payload)

    issue = result.issues[0]
    self.assertEqual(issue["code"], "item_sum_mismatch")
    self.assertEqual(
        issue["details"],
        {
            "receipt_total": "1300.00",
            "item_line_total_sum": "1249.00",
            "difference": "51.00",
            "tolerance": "1.00",
        },
    )
```

- [ ] **Step 2: Run the validator test to verify it fails**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptExtractionValidationTests.test_item_sum_mismatch_includes_compared_values
```

Expected: FAIL because the `item_sum_mismatch` issue does not include `details`.

- [ ] **Step 3: Implement the minimal validator change**

Update `_validate_item_sum()` in `receipt/extraction_review.py` so the appended issue includes:

```python
difference = abs(items_total - total)
...
"details": {
    "receipt_total": str(total),
    "item_line_total_sum": str(items_total.quantize(Decimal("0.01"))),
    "difference": str(difference.quantize(Decimal("0.01"))),
    "tolerance": str(ITEM_TOTAL_TOLERANCE),
}
```

Use the existing `_issue()` helper, then attach `details` to the returned dictionary before appending it.

- [ ] **Step 4: Run the validator test to verify it passes**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptExtractionValidationTests.test_item_sum_mismatch_includes_compared_values
```

Expected: PASS.

---

### Task 2: Render Compared Values In Both Review Locations

**Files:**
- Modify: `receipt/templates/receipt/review_detail.html`
- Modify: `receipt/static/receipt/review.css`
- Test: `receipt/tests.py`

**Interfaces:**
- Consumes: issue dictionaries with optional `details`
- Produces: review detail HTML with `data-issue-details="blocking:item_sum_mismatch"` in the Blocking issues panel
- Produces: review detail HTML with `data-issue-details="field:total:item_sum_mismatch"` under the Total field

- [ ] **Step 1: Write the failing view test**

Add this test to `ReceiptReviewViewTests`:

```python
def test_detail_renders_item_sum_mismatch_compared_values(self):
    from receipt.extraction_review import apply_extraction_result

    receipt = Receipt.objects.create(
        user_id="mismatch-review-user",
        purchase_date=timezone.now(),
        total_amount=Decimal("0.00"),
        image_url="receipt.jpg",
        status="processing",
    )
    payload = ReceiptExtractionValidationTests().valid_payload()
    payload["total"]["value"] = "1300.00"
    payload["total"]["source_text"] = "TOTAL 1300.00"
    apply_extraction_result(str(receipt.receipt_id), payload, items=None)
    self.client.force_login(self.create_staff_user())

    response = self.client.get(reverse("receipt-review:detail", args=[receipt.receipt_id]))

    self.assertEqual(response.status_code, 200)
    self.assertContains(response, 'data-issue-details="blocking:item_sum_mismatch"')
    self.assertContains(response, 'data-issue-details="field:total:item_sum_mismatch"')
    self.assertContains(response, "Total")
    self.assertContains(response, "$1300.00", count=2)
    self.assertContains(response, "Item lines")
    self.assertContains(response, "$1249.00", count=2)
    self.assertContains(response, "Difference")
    self.assertContains(response, "$51.00", count=2)
    self.assertContains(response, "Tolerance")
    self.assertContains(response, "$1.00", count=2)
```

- [ ] **Step 2: Run the view test to verify it fails**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewViewTests.test_detail_renders_item_sum_mismatch_compared_values
```

Expected: FAIL because the template does not render issue details.

- [ ] **Step 3: Render details in the Blocking issues panel**

Inside the existing `{% for issue in review.issues %}` loop, after the issue message and before source text, add:

```django
{% if issue.details %}
  <dl class="issue-detail-grid" data-issue-details="blocking:{{ issue.code }}">
    <div><dt>Total</dt><dd>${{ issue.details.receipt_total }}</dd></div>
    <div><dt>Item lines</dt><dd>${{ issue.details.item_line_total_sum }}</dd></div>
    <div><dt>Difference</dt><dd>${{ issue.details.difference }}</dd></div>
    <div><dt>Tolerance</dt><dd>${{ issue.details.tolerance }}</dd></div>
  </dl>
{% endif %}
```

- [ ] **Step 4: Render details under the Total field**

Inside the existing `{% if receipt_field_issues.total %}` block, after the badge list, add a loop over `receipt_field_issues.total`:

```django
{% for issue in receipt_field_issues.total %}
  {% if issue.details %}
    <dl class="issue-detail-grid issue-detail-grid--field" data-issue-details="field:total:{{ issue.code }}">
      <div><dt>Total</dt><dd>${{ issue.details.receipt_total }}</dd></div>
      <div><dt>Item lines</dt><dd>${{ issue.details.item_line_total_sum }}</dd></div>
      <div><dt>Difference</dt><dd>${{ issue.details.difference }}</dd></div>
      <div><dt>Tolerance</dt><dd>${{ issue.details.tolerance }}</dd></div>
    </dl>
  {% endif %}
{% endfor %}
```

- [ ] **Step 5: Add compact styling**

Add CSS to `receipt/static/receipt/review.css`:

```css
.issue-detail-grid { display:grid; gap:.35rem .75rem; grid-template-columns:repeat(2,minmax(0,1fr)); margin:.55rem 0 0; }
.issue-detail-grid div { background:rgba(255,255,255,.58); border:1px solid #fed7aa; border-radius:.45rem; padding:.4rem .5rem; }
.issue-detail-grid dt { color:#9a3412; font-size:.68rem; font-weight:700; text-transform:uppercase; }
.issue-detail-grid dd { color:#7c2d12; font-size:.86rem; font-weight:800; margin:0; }
.issue-detail-grid--field { margin-top:.45rem; }
```

- [ ] **Step 6: Run the view test to verify it passes**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewViewTests.test_detail_renders_item_sum_mismatch_compared_values
```

Expected: PASS.

---

### Task 3: Final Verification

**Files:**
- Verify: `receipt/extraction_review.py`
- Verify: `receipt/templates/receipt/review_detail.html`
- Verify: `receipt/static/receipt/review.css`
- Verify: `receipt/tests.py`

**Interfaces:**
- Consumes: all changes from Tasks 1 and 2
- Produces: verified targeted test run

- [ ] **Step 1: Run targeted receipt tests**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptExtractionValidationTests receipt.tests.ReceiptReviewViewTests
```

Expected: PASS, including existing tests and the new coverage.

- [ ] **Step 2: Inspect changed files**

Run:

```bash
git diff -- receipt/extraction_review.py receipt/templates/receipt/review_detail.html receipt/static/receipt/review.css receipt/tests.py docs/superpowers/plans/2026-07-29-item-sum-mismatch-visibility.md
```

Expected: only the planned validator details, template rendering, CSS, tests, and plan file changed.
