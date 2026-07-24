# Reprocess Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a staff-only receipt manager where active receipts can be listed, reprocessed from their existing source file, or soft-deleted.

**Architecture:** Keep receipt state changes in `receipt.services`, with Django staff views acting as thin controllers. Add `Receipt.is_active` and update operational/report queries to exclude inactive receipts. Reuse the existing Celery `process_file_task` pipeline for reprocessing.

**Tech Stack:** Django 6.0, Django ORM migrations, server-rendered Django templates, Bootstrap 5, Celery task dispatch, Django `TestCase`.

## Global Constraints

- Use soft delete only: set `Receipt.is_active=False`; do not hard-delete receipts or source files.
- The staff receipt list shows active receipts only.
- Reports and report source-image access exclude inactive receipts.
- Reprocess preserves source identity fields and clears extracted data/items/review state before enqueueing extraction.
- Reprocess and delete require staff access and POST+CSRF.
- Follow existing `receipt` app route namespace and server-rendered UI patterns.
- Use TDD: write a failing test, confirm it fails, then implement enough code to pass.

---

### Task 1: Active Receipt Model And Duplicate Lookup

**Files:**
- Modify: `receipt/models.py`
- Create: `receipt/migrations/0012_receipt_is_active.py`
- Modify: `receipt/services.py`
- Modify: `receipt/admin.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `Receipt.is_active: bool`
- Produces: active-only `unique_active_receipt_file_hash_per_user` constraint
- Updates: `get_receipt_by_user_and_file_hash(user_id: str, file_hash: str) -> Optional[ReceiptLookupResult]`

- [ ] **Step 1: Write the failing tests**

Add tests in `ReceiptFileHashTests`:

```python
def test_inactive_receipt_with_same_hash_does_not_block_new_active_receipt(self):
    receipt_services.create_receipt_with_file_hash(
        ReceiptData(user_id="user-a", image_url="media/uploads/old.jpg", status="completed"),
        "d" * 64,
    )
    Receipt.objects.update(is_active=False)

    created = receipt_services.create_receipt_with_file_hash(
        ReceiptData(user_id="user-a", image_url="media/uploads/new.jpg", status="pending"),
        "d" * 64,
    )

    self.assertTrue(created.created)
    self.assertEqual(Receipt.objects.filter(user_id="user-a", file_hash="d" * 64).count(), 2)
    self.assertEqual(Receipt.objects.filter(is_active=True).count(), 1)

def test_file_hash_lookup_ignores_inactive_receipts(self):
    receipt_services.create_receipt_with_file_hash(
        ReceiptData(user_id="user-a", image_url="media/uploads/old.jpg", status="completed"),
        "e" * 64,
    )
    Receipt.objects.update(is_active=False)

    result = receipt_services.get_receipt_by_user_and_file_hash("user-a", "e" * 64)

    self.assertIsNone(result)
```

Add a model metadata test in `ReceiptReviewStatusTests`:

```python
def test_active_file_hash_constraint_is_scoped_to_active_receipts(self):
    constraints = {constraint.name: constraint for constraint in Receipt._meta.constraints}

    self.assertIn("unique_active_receipt_file_hash_per_user", constraints)
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptFileHashTests receipt.tests.ReceiptReviewStatusTests -v 2
```

Expected: FAIL because `is_active` and the active-only constraint do not exist.

- [ ] **Step 3: Implement the model and lookup changes**

In `receipt/models.py`, add:

```python
is_active = models.BooleanField(default=True, db_index=True)
```

Replace the constraint with:

```python
models.UniqueConstraint(
    fields=['user_id', 'file_hash'],
    condition=models.Q(file_hash__isnull=False, is_active=True),
    name='unique_active_receipt_file_hash_per_user',
)
```

In `receipt/services.py`, update:

```python
receipt = Receipt.objects.get(user_id=user_id, file_hash=file_hash, is_active=True)
```

In `receipt/admin.py`, include `is_active` in `ReceiptAdmin.list_display` and `ReceiptAdmin.list_filter`.

Create migration `receipt/migrations/0012_receipt_is_active.py` that removes `unique_receipt_file_hash_per_user`, adds `is_active`, and adds `unique_active_receipt_file_hash_per_user`.

- [ ] **Step 4: Verify the tests pass**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptFileHashTests receipt.tests.ReceiptReviewStatusTests -v 2
```

Expected: PASS.

---

### Task 2: Receipt Reprocess And Deactivate Services

**Files:**
- Modify: `receipt/services.py`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `reset_receipt_for_reprocessing(receipt_id: str) -> Receipt`
- Produces: `deactivate_receipt(receipt_id: str) -> Receipt`
- Produces: `infer_receipt_file_type(image_url: str) -> str`

- [ ] **Step 1: Write the failing tests**

Add `ReceiptManagementServiceTests`:

```python
class ReceiptManagementServiceTests(TestCase):
    def create_completed_receipt(self, *, image_url="media/uploads/source.jpg"):
        receipt = Receipt.objects.create(
            user_id="manager-user",
            purchase_date=timezone.now() - timezone.timedelta(days=2),
            total_amount=Decimal("125.50"),
            subtotal_amount=Decimal("140.00"),
            discount_amount=Decimal("14.50"),
            store_name="chedraui",
            image_url=image_url,
            file_hash="f" * 64,
            source_type="manual_upload",
            source_metadata={"origin": "test"},
            status="completed",
            extracted_text="old text",
            extraction_result={"old": "payload"},
        )
        ReceiptItem.objects.create(
            receipt=receipt,
            name="Milk",
            price=50.00,
            quantity=2,
            category="dairy",
        )
        ReceiptExtractionReview.objects.create(
            receipt=receipt,
            status="needs_review",
            overall_confidence=0.4,
            issues=[{"code": "old"}],
            raw_extraction={"old": "raw"},
        )
        return receipt

    def test_reset_receipt_for_reprocessing_clears_extracted_state_and_items(self):
        receipt = self.create_completed_receipt()

        reset = receipt_services.reset_receipt_for_reprocessing(str(receipt.receipt_id))

        receipt.refresh_from_db()
        self.assertEqual(reset.receipt_id, receipt.receipt_id)
        self.assertEqual(receipt.status, "pending")
        self.assertEqual(receipt.total_amount, Decimal("0.00"))
        self.assertIsNone(receipt.subtotal_amount)
        self.assertIsNone(receipt.discount_amount)
        self.assertIsNone(receipt.store_name)
        self.assertIsNone(receipt.extracted_text)
        self.assertIsNone(receipt.extraction_result)
        self.assertEqual(receipt.items.count(), 0)
        self.assertFalse(ReceiptExtractionReview.objects.filter(receipt=receipt).exists())

    def test_reset_receipt_for_reprocessing_preserves_source_identity(self):
        receipt = self.create_completed_receipt()

        receipt_services.reset_receipt_for_reprocessing(str(receipt.receipt_id))

        receipt.refresh_from_db()
        self.assertEqual(receipt.user_id, "manager-user")
        self.assertEqual(receipt.file_hash, "f" * 64)
        self.assertEqual(receipt.image_url, "media/uploads/source.jpg")
        self.assertEqual(receipt.source_type, "manual_upload")
        self.assertEqual(receipt.source_metadata, {"origin": "test"})
        self.assertTrue(receipt.is_active)

    def test_deactivate_receipt_sets_is_active_false(self):
        receipt = self.create_completed_receipt()

        receipt_services.deactivate_receipt(str(receipt.receipt_id))

        receipt.refresh_from_db()
        self.assertFalse(receipt.is_active)
        self.assertEqual(receipt.items.count(), 1)

    def test_infer_receipt_file_type_uses_pdf_extension(self):
        self.assertEqual(receipt_services.infer_receipt_file_type("media/uploads/source.pdf"), "pdf")
        self.assertEqual(receipt_services.infer_receipt_file_type("media/uploads/source.JPG"), "image")
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptManagementServiceTests -v 2
```

Expected: FAIL because the new service functions do not exist.

- [ ] **Step 3: Implement service functions**

In `receipt/services.py`, add transactional implementations:

```python
def reset_receipt_for_reprocessing(receipt_id: str) -> Receipt:
    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id, is_active=True)
        receipt.items.all().delete()
        ReceiptExtractionReview.objects.filter(receipt=receipt).delete()
        receipt.status = "pending"
        receipt.purchase_date = timezone.now()
        receipt.total_amount = Decimal("0.00")
        receipt.subtotal_amount = None
        receipt.discount_amount = None
        receipt.store_name = None
        receipt.extracted_text = None
        receipt.extraction_result = None
        receipt.save(update_fields=[
            "status",
            "purchase_date",
            "total_amount",
            "subtotal_amount",
            "discount_amount",
            "store_name",
            "extracted_text",
            "extraction_result",
            "updated_at",
        ])
        return receipt

def deactivate_receipt(receipt_id: str) -> Receipt:
    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id, is_active=True)
        receipt.is_active = False
        receipt.save(update_fields=["is_active", "updated_at"])
        return receipt

def infer_receipt_file_type(image_url: str) -> str:
    return "pdf" if Path(urlparse(image_url).path).suffix.lower() == ".pdf" else "image"
```

Add imports for `urlparse` and `ReceiptExtractionReview` as needed.

- [ ] **Step 4: Verify the tests pass**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptManagementServiceTests -v 2
```

Expected: PASS.

---

### Task 3: Staff Receipt List And Actions

**Files:**
- Modify: `receipt/views.py`
- Modify: `receipt/urls.py`
- Create: `receipt/templates/receipt/list.html`
- Modify: `templates/base.html`
- Test: `receipt/tests.py`

**Interfaces:**
- Produces: `receipt_list(request)`
- Produces: `reprocess_receipt(request, receipt_id)`
- Produces: `delete_receipt(request, receipt_id)`
- Uses: `receipt_services.reset_receipt_for_reprocessing()`
- Uses: `receipt_services.deactivate_receipt()`
- Uses: `receipt_services.infer_receipt_file_type()`

- [ ] **Step 1: Write the failing tests**

Add `ReceiptManagementViewTests`:

```python
class ReceiptManagementViewTests(TestCase):
    def create_staff_user(self):
        return get_user_model().objects.create_user(username="manager", password="password", is_staff=True)

    def create_regular_user(self):
        return get_user_model().objects.create_user(username="viewer", password="password", is_staff=False)

    def create_receipt(self, *, is_active=True, status="completed", image_url="media/uploads/source.jpg"):
        receipt = Receipt.objects.create(
            user_id="manager-user",
            purchase_date=timezone.now(),
            total_amount=Decimal("75.25"),
            store_name="soriana",
            image_url=image_url,
            status=status,
            is_active=is_active,
        )
        ReceiptItem.objects.create(receipt=receipt, name="Apples", price=25.00, quantity=3, category="fruits")
        return receipt

    def test_receipt_list_requires_staff(self):
        self.client.force_login(self.create_regular_user())

        response = self.client.get(reverse("receipt-review:list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_staff_receipt_list_shows_active_receipts_and_hides_inactive(self):
        active = self.create_receipt(is_active=True)
        inactive = self.create_receipt(is_active=False)
        self.client.force_login(self.create_staff_user())

        response = self.client.get(reverse("receipt-review:list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(active.receipt_id))
        self.assertNotContains(response, str(inactive.receipt_id))
        self.assertContains(response, "soriana")
        self.assertContains(response, "$75.25")
        self.assertContains(response, "1")
        self.assertContains(response, "Reprocess")
        self.assertContains(response, "Delete")

    @patch("receipt.views.process_file_task.delay")
    def test_reprocess_action_resets_receipt_and_enqueues_processing(self, delay):
        receipt = self.create_receipt()
        self.client.force_login(self.create_staff_user())

        response = self.client.post(reverse("receipt-review:reprocess", args=[receipt.receipt_id]))

        receipt.refresh_from_db()
        self.assertRedirects(response, reverse("receipt-review:list"))
        self.assertEqual(receipt.status, "pending")
        self.assertEqual(receipt.items.count(), 0)
        delay.assert_called_once_with(
            receipt_id=str(receipt.receipt_id),
            file_path="media/uploads/source.jpg",
            file_type="image",
        )

    @patch("receipt.views.process_file_task.delay")
    def test_reprocess_action_refuses_missing_source_path(self, delay):
        receipt = self.create_receipt(image_url="")
        self.client.force_login(self.create_staff_user())

        response = self.client.post(reverse("receipt-review:reprocess", args=[receipt.receipt_id]))

        receipt.refresh_from_db()
        self.assertRedirects(response, reverse("receipt-review:list"))
        self.assertEqual(receipt.status, "completed")
        self.assertEqual(receipt.items.count(), 1)
        delay.assert_not_called()

    def test_delete_action_requires_staff(self):
        receipt = self.create_receipt()
        self.client.force_login(self.create_regular_user())

        response = self.client.post(reverse("receipt-review:delete", args=[receipt.receipt_id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_delete_action_deactivates_and_hides_receipt(self):
        receipt = self.create_receipt()
        self.client.force_login(self.create_staff_user())

        response = self.client.post(reverse("receipt-review:delete", args=[receipt.receipt_id]), follow=True)

        receipt.refresh_from_db()
        self.assertFalse(receipt.is_active)
        self.assertNotContains(response, str(receipt.receipt_id))
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptManagementViewTests -v 2
```

Expected: FAIL because the routes and views do not exist.

- [ ] **Step 3: Implement views and URLs**

In `receipt/urls.py`, add:

```python
path("all/", views.receipt_list, name="list"),
path("<uuid:receipt_id>/reprocess/", views.reprocess_receipt, name="reprocess"),
path("<uuid:receipt_id>/delete/", views.delete_receipt, name="delete"),
```

In `receipt/views.py`, add staff-only views that:

- list `Receipt.objects.filter(is_active=True).annotate(item_count=Count("items")).order_by("-updated_at")`
- reprocess only active receipts with an `image_url`
- call `process_file_task.delay(receipt_id=str(receipt.receipt_id), file_path=receipt.image_url, file_type=file_type)`
- deactivate active receipts
- redirect to `receipt-review:list` with messages

- [ ] **Step 4: Implement template and nav**

Create `receipt/templates/receipt/list.html` using the existing review table/card styles. Use Bootstrap dropdowns and modals for per-row actions.

In `templates/base.html`, add:

```django
<li class="nav-item">
  <a class="nav-link {% block receipts_nav_class %}{% endblock %}" href="{% url 'receipt-review:list' %}">Receipts</a>
</li>
```

- [ ] **Step 5: Verify the tests pass**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptManagementViewTests -v 2
```

Expected: PASS.

---

### Task 4: Exclude Inactive Receipts From Reports And Review Queue

**Files:**
- Modify: `reports/services.py`
- Modify: `reports/views.py`
- Modify: `receipt/views.py`
- Test: `receipt/tests.py`
- Test: `reports/tests.py`

**Interfaces:**
- Updates report query filters to include active receipts only.
- Updates review queue active filter.

- [ ] **Step 1: Write the failing tests**

In `ReceiptReviewViewTests`, add:

```python
def test_review_queue_hides_inactive_receipts(self):
    receipt = self.create_review_receipt()
    receipt.is_active = False
    receipt.save(update_fields=["is_active"])
    self.client.force_login(self.create_staff_user())

    response = self.client.get(reverse("receipt-review:queue"))

    self.assertEqual(response.status_code, 200)
    self.assertNotContains(response, str(receipt.receipt_id))
```

In `ReceiptReviewReportExclusionTests`, add:

```python
def test_reports_exclude_inactive_completed_receipts(self):
    active = self.create_receipt_with_item(status="completed", price=10.00)
    inactive = self.create_receipt_with_item(status="completed", price=99.00)
    inactive.is_active = False
    inactive.save(update_fields=["is_active"])

    from reports.services import CategorySpendingService, ReceiptItemsService

    item_report = ReceiptItemsService.build_report({})
    category_report = CategorySpendingService.build_report({})

    self.assertEqual(item_report.item_count, 1)
    self.assertEqual(item_report.rows[0].receipt_id, str(active.receipt_id))
    self.assertEqual(category_report.grand_total, Decimal("10.00"))
```

In `reports/tests.py`, add an endpoint test that creates an inactive completed receipt with a local image and expects 404 from `reports:receipt-ticket-image`.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptReviewViewTests receipt.tests.ReceiptReviewReportExclusionTests reports.tests -v 2
```

Expected: FAIL because inactive filtering is not implemented.

- [ ] **Step 3: Implement active filters**

In `reports/services.py`, add `receipt__is_active=True` to `ReceiptItem` queries and `is_active=True` to receipt store option queries.

In `reports/views.py`, require `is_active=True` in `receipt_ticket_image()`.

In `receipt/views.py`, require `receipt__is_active=True` in `review_queue()`.

- [ ] **Step 4: Verify the tests pass**

Run:

```bash
python3 manage.py test receipt.tests.ReceiptReviewViewTests receipt.tests.ReceiptReviewReportExclusionTests reports.tests -v 2
```

Expected: PASS.

---

### Task 5: Final Verification And Cleanup

**Files:**
- Review all modified files.

**Interfaces:**
- Confirms the feature is complete and stable.

- [ ] **Step 1: Run focused receipt tests**

Run:

```bash
python3 manage.py test receipt.tests -v 2
```

Expected: PASS.

- [ ] **Step 2: Run focused report tests**

Run:

```bash
python3 manage.py test reports.tests -v 2
```

Expected: PASS.

- [ ] **Step 3: Run full Django test suite**

Run:

```bash
python3 manage.py test -v 2
```

Expected: PASS, unless local database dependencies are unavailable. If unavailable, report the exact blocker.

- [ ] **Step 4: Inspect migration and diff**

Run:

```bash
python3 manage.py makemigrations --check --dry-run
git diff --check
git status --short
```

Expected: no missing migrations, no whitespace errors, and only intended files changed.
