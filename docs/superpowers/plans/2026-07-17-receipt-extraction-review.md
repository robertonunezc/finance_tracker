# Receipt Extraction Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validate receipt LLM extractions, capture confidence/evidence, route suspicious receipts to staff review, and block reporting until review approval.

**Architecture:** Keep workflow state in the `receipt` app. Add a dedicated `ReceiptExtractionReview` model for the queue and issue state, a focused `receipt.extraction_review` service for validation/application/correction, and server-rendered staff review pages. Update the extraction task so it hands enriched extraction data to the review service instead of directly marking every successful parse as completed.

**Tech Stack:** Django 6.0, Django ORM, PostgreSQL, Celery, Pydantic structured OpenAI responses, Bootstrap 5 templates.

## Global Constraints

- The first implementation is scoped to the current receipt pipeline.
- Receipts with blocking validation issues must be marked `needs_review`.
- Reports must continue to include only `Receipt.status == "completed"`.
- Manual review pages must require Django staff authentication.
- Original LLM extraction data must be preserved separately from corrected values.
- Approval must be blocked while blocking validation issues remain.
- Do not add force approval in this version.

---

### Task 1: Review Model And Status

**Files:**
- Modify: `receipt/models.py`
- Modify: `receipt/admin.py`
- Create: `receipt/migrations/0007_receipt_extraction_review.py`
- Modify: `telegram_bot/process_message.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `ReceiptExtractionReview` model with `receipt`, `status`, `overall_confidence`, `issues`, `raw_extraction`, `corrected_payload`, `approved_by`, `approved_at`, timestamps.
- Produces: `needs_review` as a valid `Receipt.status`.
- Produces: duplicate action handling where `needs_review` returns `skip_needs_review`.

- [ ] **Step 1: Write failing status and duplicate tests**

Add to `receipt/tests.py`:

```python
class ReceiptReviewStatusTests(TestCase):
    def test_needs_review_is_valid_receipt_status(self):
        receipt = Receipt.objects.create(
            user_id="review-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("10.00"),
            image_url="receipt.jpg",
            status="needs_review",
        )

        self.assertEqual(receipt.status, "needs_review")


class ReceiptDuplicateActionTests(TestCase):
    def test_needs_review_duplicate_skips_new_task(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("needs_review"), "skip_needs_review")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptReviewStatusTests receipt.tests.ReceiptDuplicateActionTests`

Expected: failure because `needs_review` is not in the status choices or duplicate action map.

- [ ] **Step 3: Implement status/model/admin/migration**

Update `receipt/models.py`:

```python
STATUS_CHOICES = [
    ("pending", "Pending"),
    ("processing", "Processing"),
    ("needs_review", "Needs review"),
    ("completed", "Completed"),
    ("failed", "Failed"),
]

REVIEW_STATUS_CHOICES = [
    ("needs_review", "Needs review"),
    ("approved", "Approved"),
]

class ReceiptExtractionReview(models.Model):
    receipt = models.OneToOneField(
        Receipt,
        on_delete=models.CASCADE,
        related_name="extraction_review",
    )
    status = models.CharField(max_length=20, choices=REVIEW_STATUS_CHOICES, default="needs_review")
    overall_confidence = models.FloatField(default=0.0)
    issues = models.JSONField(default=list, blank=True)
    raw_extraction = models.JSONField(default=dict, blank=True)
    corrected_payload = models.JSONField(null=True, blank=True)
    approved_by = models.CharField(max_length=255, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

Update `receipt/admin.py` to register `ReceiptExtractionReview` and add a one-to-one inline to `ReceiptAdmin`.

Create migration `0007_receipt_extraction_review.py` with `AlterField` for status choices and `CreateModel` for `ReceiptExtractionReview`.

Update `telegram_bot/process_message.py`:

```python
if status == "needs_review":
    return "skip_needs_review"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptReviewStatusTests receipt.tests.ReceiptDuplicateActionTests`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add receipt/models.py receipt/admin.py receipt/migrations/0007_receipt_extraction_review.py telegram_bot/process_message.py receipt/tests.py
git commit -m "feat: add receipt extraction review model"
```

### Task 2: Validation Service

**Files:**
- Create: `receipt/extraction_review.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `validate_receipt_extraction(payload: Mapping[str, Any]) -> ValidationResult`
- Produces: `parse_amounts_from_source_text(source_text: str) -> list[Decimal]`
- Produces issue dictionaries with `path`, `code`, `severity`, `message`, `extracted_value`, `source_text`.

- [ ] **Step 1: Write failing validator tests**

Add tests for:

```python
validate_receipt_extraction(valid_payload).requires_review is False
validate_receipt_extraction(low_confidence_payload).issues[0]["code"] == "low_confidence"
validate_receipt_extraction(source_mismatch_payload).issues[0]["code"] == "source_amount_mismatch"
validate_receipt_extraction(item_sum_mismatch_payload).issues[0]["code"] == "item_sum_mismatch"
validate_receipt_extraction(missing_required_payload).issues[0]["code"] == "missing_required_value"
parse_amounts_from_source_text("AMZN MX MARKETPLACE  1,249.00") == [Decimal("1249.00")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptExtractionValidationTests`

Expected: FAIL because `receipt.extraction_review` does not exist.

- [ ] **Step 3: Implement validator**

Create `receipt/extraction_review.py` with:

```python
CONFIDENCE_THRESHOLD = 0.80
ITEM_TOTAL_TOLERANCE = Decimal("1.00")

@dataclass(frozen=True)
class ValidationResult:
    overall_confidence: float
    issues: list[dict[str, Any]]

    @property
    def requires_review(self) -> bool:
        return any(issue.get("severity") == "blocking" for issue in self.issues)
```

Implement helpers for field access, decimal parsing, confidence checks, amount-source checks, item sum checks, and missing required checks.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptExtractionValidationTests`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add receipt/extraction_review.py receipt/tests.py
git commit -m "feat: validate receipt extraction confidence"
```

### Task 3: Apply Extraction Results

**Files:**
- Modify: `receipt/extraction_review.py`
- Modify: `extract_info/services.py`
- Modify: `extract_info/tasks.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `build_extraction_payload(ticket, items: list[ReceiptItemData] | None = None) -> dict[str, Any]`
- Produces: `apply_extraction_result(receipt_id: str, ticket: Any, items: list[ReceiptItemData]) -> ExtractionApplicationResult`
- Produces: task behavior that saves either `completed` or `needs_review`.

- [ ] **Step 1: Write failing application tests**

Add tests for:

```python
result = apply_extraction_result(receipt_id, valid_payload, items)
self.assertEqual(result.status, "completed")
self.assertFalse(ReceiptExtractionReview.objects.filter(receipt=receipt).exists())

result = apply_extraction_result(receipt_id, low_confidence_payload, items)
self.assertEqual(result.status, "needs_review")
self.assertTrue(ReceiptExtractionReview.objects.filter(receipt=receipt).exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptExtractionApplicationTests`

Expected: FAIL because application functions do not exist.

- [ ] **Step 3: Implement extraction payload/application**

In `extract_info/services.py`, wrap structured fields with value/source/confidence Pydantic models and update the prompt to request source evidence and confidence.

In `receipt/extraction_review.py`, implement payload serialization and `apply_extraction_result`.

In `extract_info/tasks.py`, replace direct receipt update with:

```python
application_result = extraction_review.apply_extraction_result(
    receipt_id=receipt_id,
    ticket=ticket,
    items=items,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptExtractionApplicationTests extract_info.tests`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add receipt/extraction_review.py extract_info/services.py extract_info/tasks.py receipt/tests.py
git commit -m "feat: apply receipt extraction validation"
```

### Task 4: Manual Review Corrections And Approval

**Files:**
- Modify: `receipt/extraction_review.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `save_review_corrections(receipt_id: str, form_data: Mapping[str, Any]) -> ReviewActionResult`
- Produces: `approve_review(receipt_id: str, form_data: Mapping[str, Any], user: Any) -> ReviewActionResult`
- Produces: blocking approval until validation passes.

- [ ] **Step 1: Write failing correction tests**

Add tests for:

```python
result = approve_review(receipt_id, wrong_post_data, user="admin")
self.assertFalse(result.approved)
self.assertEqual(Receipt.objects.get(receipt_id=receipt_id).status, "needs_review")

result = approve_review(receipt_id, corrected_post_data, user="admin")
self.assertTrue(result.approved)
self.assertEqual(Receipt.objects.get(receipt_id=receipt_id).status, "completed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptReviewCorrectionTests`

Expected: FAIL because correction/approval APIs do not exist.

- [ ] **Step 3: Implement correction and approval APIs**

Parse receipt fields and item rows from form data, update `Receipt` and `ReceiptItem`, rebuild a human-confirmed corrected payload with confidence `1.0`, rerun validation, update `ReceiptExtractionReview`, and only mark completed when `approve=True` and validation has no blocking issues.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptReviewCorrectionTests`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add receipt/extraction_review.py receipt/tests.py
git commit -m "feat: approve corrected receipt extractions"
```

### Task 5: Staff Review UI

**Files:**
- Create: `receipt/urls.py`
- Modify: `receipt/views.py`
- Modify: `finance_tracker/urls.py`
- Modify: `templates/base.html`
- Create: `receipt/templates/receipt/review_queue.html`
- Create: `receipt/templates/receipt/review_detail.html`
- Create: `receipt/static/receipt/review.css`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: route name `receipt-review:queue`
- Produces: route name `receipt-review:detail`
- Consumes: `save_review_corrections` and `approve_review`.

- [ ] **Step 1: Write failing view tests**

Add tests that unauthenticated users are redirected to `/admin/login/`, non-staff users receive forbidden/redirect behavior, staff users can load the queue, and staff users can approve a corrected receipt through POST.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptReviewViewTests`

Expected: FAIL because review URLs and templates do not exist.

- [ ] **Step 3: Implement staff views, URLs, nav, and templates**

Add staff-only queue and detail views. The detail POST should branch on `action=save` or `action=approve`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptReviewViewTests`

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add receipt/urls.py receipt/views.py finance_tracker/urls.py templates/base.html receipt/templates/receipt/review_queue.html receipt/templates/receipt/review_detail.html receipt/static/receipt/review.css receipt/tests.py
git commit -m "feat: add receipt extraction review UI"
```

### Task 6: Reporting And End-To-End Verification

**Files:**
- Modify: `reports/services.py` only if tests show `needs_review` leaks into reports.
- Test: `receipt/tests.py`

**Interfaces:**
- Consumes: existing report filtering on `Receipt.status == "completed"`.
- Produces: verification that `needs_review` receipts do not appear in trusted reports.

- [ ] **Step 1: Write report exclusion test**

Add a test that creates one `completed` receipt item and one `needs_review` receipt item, then confirms `ReceiptItemsService.build_report({})` and `CategorySpendingService.build_report({})` only include the completed item.

- [ ] **Step 2: Run test**

Run: `python manage.py test receipt.tests.ReceiptReviewReportExclusionTests`

Expected: PASS with existing report filters. If it fails, fix the report filter to require `receipt__status="completed"`.

- [ ] **Step 3: Run focused workflow tests**

Run:

```bash
python manage.py test receipt.tests extract_info.tests
```

Expected: PASS.

- [ ] **Step 4: Run full test suite**

Run:

```bash
python manage.py test
```

Expected: PASS if the local PostgreSQL test database is available.

- [ ] **Step 5: Final commit**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: working tree clean and feature commits present.
