# Correction Feedback Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make approved corrected item categories feed the existing category-memory lookup by storing embeddings on corrected final `ReceiptItem` rows.

**Architecture:** Keep correction storage in the existing `receipt` app approval flow and reuse `ReceiptItem.embedding` as the memory store consumed by the current pgvector lookup. Add an injectable embedding function boundary so tests do not call OpenAI and approval can tolerate external embedding failures.

**Tech Stack:** Django 6.0, Django `TestCase`, Python `Decimal`, pgvector `VectorField`, existing OpenAI embedding helper.

## Global Constraints

- Do not auto-apply old totals, quantities, line totals, or item counts to future receipts.
- Do not fine-tune a model in this slice.
- Do not change the receipt extraction prompt in this slice.
- Do not add a new correction dashboard or eval report in this slice.
- Do not add a new database table unless implementation shows the existing `ReceiptItem.embedding` field cannot support the category-memory use case.
- Approval should not fail only because category-memory embedding failed.

---

### Task 1: Add Embedding Boundary To Review Approval

**Files:**
- Modify: `receipt/extraction_review.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Consumes: `approve_review(receipt_id: str, form_data: Mapping[str, Any], user: Any) -> ReviewActionResult`
- Produces: `_apply_review_action(receipt_id, form_data, approve, user, item_embedding_fn=None) -> ReviewActionResult`
- Produces: `_replace_receipt_items(receipt, payload, items, item_embedding_fn=None) -> None`

- [ ] **Step 1: Patch embedding generation by default in correction tests**

Add this setup method to `ReceiptReviewCorrectionTests` so existing approval tests stay offline after approval starts generating embeddings:

```python
def setUp(self):
    self.generate_embedding_patcher = patch(
        "extract_info.services.generate_embedding",
        return_value=[0.1] * 1536,
    )
    self.generate_embedding_patcher.start()
    self.addCleanup(self.generate_embedding_patcher.stop)
```

Add the same setup method to `ReceiptReviewViewTests` because its approval POST exercises the same embedding path through the Django view.

- [ ] **Step 2: Write failing test for approved corrected item embeddings**

Add this test to `ReceiptReviewCorrectionTests`:

```python
def test_approval_stores_embedding_for_corrected_item_category_memory(self):
    from receipt.extraction_review import approve_review

    receipt = self.create_review_receipt()
    data = self.post_data(price="1249.00")
    data["item_0_name"] = "Corrected Keyboard"
    data["item_0_category"] = "electronics"
    embedding = [0.1] * 1536

    with patch(
        "extract_info.services.generate_embedding",
        return_value=embedding,
    ) as generate_embedding:
        result = approve_review(str(receipt.receipt_id), data, user="admin")

    self.assertTrue(result.approved)
    item = ReceiptItem.objects.get(receipt=receipt)
    self.assertEqual(item.name, "Corrected Keyboard")
    self.assertEqual(item.category, "electronics")
    self.assertEqual(list(item.embedding), embedding)
    generate_embedding.assert_called_once_with("Corrected Keyboard")
```

- [ ] **Step 3: Run test to verify it fails**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests.test_approval_stores_embedding_for_corrected_item_category_memory
```

Expected: FAIL because approval does not generate embeddings for corrected items.

- [ ] **Step 4: Implement minimal embedding boundary**

In `receipt/extraction_review.py`:

1. Add imports:

```python
from collections.abc import Callable
from extract_info import services as extract_info_service
```

2. Update `_apply_review_action()` signature:

```python
def _apply_review_action(
    receipt_id: str,
    form_data: Mapping[str, Any],
    *,
    approve: bool,
    user: Any,
    item_embedding_fn: Callable[[str], list[float]] | None = None,
) -> ReviewActionResult:
```

3. Before replacing items, choose the embedding function only for approved receipts:

```python
if approved and item_embedding_fn is None:
    item_embedding_fn = extract_info_service.generate_embedding
if not approved:
    item_embedding_fn = None
```

4. Pass it to `_replace_receipt_items()`:

```python
_replace_receipt_items(receipt, corrected_payload, items=None, item_embedding_fn=item_embedding_fn)
```

5. Update `_replace_receipt_items()` signature:

```python
def _replace_receipt_items(
    receipt: Receipt,
    payload: Mapping[str, Any],
    items: list[ReceiptItemData] | None,
    *,
    item_embedding_fn: Callable[[str], list[float]] | None = None,
) -> None:
```

6. When `items is None`, pass `item_embedding_fn` into `_payload_item_to_dataclass()`.

7. Update `_payload_item_to_dataclass()` signature and set embedding:

```python
def _payload_item_to_dataclass(
    item: Mapping[str, Any],
    *,
    item_embedding_fn: Callable[[str], list[float]] | None = None,
) -> ReceiptItemData:
    name = str(_field_raw_value(item.get("name")) or "")
    embedding = item_embedding_fn(name) if item_embedding_fn and name else None
    return ReceiptItemData(
        name=name,
        unit_price=_optional_float(_field_decimal(item.get("unit_price"))),
        line_total=float(_field_decimal(item.get("line_total")) or Decimal("0.00")),
        quantity=_field_positive_quantity(item.get("quantity")) or Decimal("1.000"),
        category=str(_field_raw_value(item.get("category")) or Category.OTHER),
        embedding=embedding,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests.test_approval_stores_embedding_for_corrected_item_category_memory
```

Expected: PASS.

---

### Task 2: Make Embedding Failures Non-Blocking

**Files:**
- Modify: `receipt/extraction_review.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Consumes: `_payload_item_to_dataclass(item, item_embedding_fn=None) -> ReceiptItemData`
- Produces: approved `ReceiptItem` rows with `embedding=None` when embedding generation fails

- [ ] **Step 1: Write failing test for non-blocking embedding failure**

Add this test to `ReceiptReviewCorrectionTests`:

```python
def test_approval_succeeds_when_corrected_item_embedding_fails(self):
    from receipt.extraction_review import approve_review

    receipt = self.create_review_receipt()
    data = self.post_data(price="1249.00")
    data["item_0_name"] = "Corrected Keyboard"

    with self.assertLogs("receipt.extraction_review", level="WARNING"):
        with patch(
            "extract_info.services.generate_embedding",
            side_effect=RuntimeError("embedding failed"),
        ):
            result = approve_review(str(receipt.receipt_id), data, user="admin")

    self.assertTrue(result.approved)
    item = ReceiptItem.objects.get(receipt=receipt)
    self.assertEqual(item.name, "Corrected Keyboard")
    self.assertIsNone(item.embedding)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests.test_approval_succeeds_when_corrected_item_embedding_fails
```

Expected: FAIL because embedding failure propagates and blocks approval.

- [ ] **Step 3: Implement non-blocking embedding helper**

Add helper in `receipt/extraction_review.py`:

```python
def _item_embedding(name: str, item_embedding_fn: Callable[[str], list[float]] | None) -> list[float] | None:
    if not item_embedding_fn or not name:
        return None
    try:
        return item_embedding_fn(name)
    except Exception:
        logger.warning("Failed to generate corrected item embedding for %s", name, exc_info=True)
        return None
```

Use `_item_embedding(name, item_embedding_fn)` inside `_payload_item_to_dataclass()`.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests.test_approval_succeeds_when_corrected_item_embedding_fails
```

Expected: PASS.

---

### Task 3: Keep Blocked Corrections Out Of Trusted Category Memory

**Files:**
- Modify: `receipt/extraction_review.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Consumes: `approve_review(receipt_id, form_data, user) -> ReviewActionResult`
- Produces: no embedding generation when validation still blocks approval

- [ ] **Step 1: Write failing test for blocked approvals**

Add this test to `ReceiptReviewCorrectionTests`:

```python
def test_blocked_approval_does_not_store_corrected_item_embedding(self):
    from receipt.extraction_review import approve_review

    receipt = self.create_review_receipt()
    data = self.post_data(total="0.00", price="0.00")
    data["item_0_name"] = "Corrected Keyboard"

    with patch("extract_info.services.generate_embedding") as generate_embedding:
        result = approve_review(str(receipt.receipt_id), data, user="admin")

    self.assertFalse(result.approved)
    generate_embedding.assert_not_called()
    item = ReceiptItem.objects.get(receipt=receipt)
    self.assertEqual(item.name, "Corrected Keyboard")
    self.assertIsNone(item.embedding)
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests.test_blocked_approval_does_not_store_corrected_item_embedding
```

Expected after Task 1 implementation: PASS if blocked approvals already suppress embedding generation. If it fails, adjust `_apply_review_action()` so `item_embedding_fn` is set only when `approved` is true.

- [ ] **Step 3: Run full correction test class**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests
```

Expected: PASS.

---

### Task 4: Final Verification

**Files:**
- Verify: `receipt/extraction_review.py`
- Verify: `receipt/tests.py`
- Verify: `docs/superpowers/plans/2026-07-29-correction-feedback-loop.md`

**Interfaces:**
- Consumes: all previous task changes
- Produces: verified feedback-loop implementation branch

- [ ] **Step 1: Run targeted tests**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test receipt.tests.ReceiptReviewCorrectionTests receipt.tests.ReceiptExtractionApplicationTests extract_info.tests.ProcessFileTaskReviewIntegrationTests
```

Expected: PASS.

- [ ] **Step 2: Run full Django test suite**

Run:

```bash
/Users/robertonunez/Documents/Dev/finance_tracker/.venv/bin/python manage.py test
```

Expected: PASS. Existing `test_openai.py` fake-key 401 traces may still print during discovery, but the command should exit 0.

- [ ] **Step 3: Inspect diff**

Run:

```bash
git diff -- receipt/extraction_review.py receipt/tests.py docs/superpowers/plans/2026-07-29-correction-feedback-loop.md
git diff --check
```

Expected: only the planned review-approval embedding boundary, tests, and plan file changed, with no whitespace errors.
