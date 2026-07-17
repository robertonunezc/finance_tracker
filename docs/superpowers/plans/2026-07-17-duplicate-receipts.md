# Duplicate Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent duplicate receipt records when a Telegram user uploads the exact same receipt image more than once.

**Architecture:** Store a SHA-256 file hash on each new `Receipt` and enforce uniqueness per `(user_id, file_hash)` for non-null hashes. Keep duplicate lookup and race-safe creation in `receipt.services`; keep Telegram upload orchestration in `telegram_bot.process_message`; keep extraction retries on the same receipt ID.

**Tech Stack:** Django 6.0, PostgreSQL, Celery, python-telegram-bot, Python stdlib `hashlib`.

## Global Constraints

- De-duplicate per user, not globally across all users.
- Detect exact byte duplicates only.
- Skip extraction for completed duplicates.
- Do not enqueue duplicate work for pending or processing duplicates.
- Retry failed duplicates by reusing the same receipt.
- Use the existing `failed` status, not `error`.
- Existing receipts with `NULL` file hashes remain valid.

---

## File Structure

- `receipt/models.py`: add `Receipt.file_hash` and the conditional unique constraint.
- `receipt/migrations/0006_receipt_file_hash_unique.py`: add the schema migration.
- `receipt/dataclasses.py`: add `ReceiptLookupResult` for duplicate decisions.
- `receipt/services.py`: add SHA-256 hashing, hash lookup, race-safe create, and include hash metadata in results.
- `receipt/tests.py`: add focused service tests for hash and duplicate behavior.
- `telegram_bot/process_message.py`: compute hash before upload, branch on duplicate status, and enqueue only when needed.
- `extract_info/tasks.py`: replace the invalid exhausted-retry status with `failed`.
- `extract_info/tests.py`: add a regression test for the failure status helper.

---

### Task 1: Receipt Hash Data Model and Service Contract

**Files:**
- Modify: `receipt/tests.py`
- Modify: `receipt/models.py`
- Modify: `receipt/dataclasses.py`
- Modify: `receipt/services.py`
- Create: `receipt/migrations/0006_receipt_file_hash_unique.py`

**Interfaces:**
- Produces: `receipt.services.compute_file_sha256(file_path: str) -> str`
- Produces: `receipt.services.get_receipt_by_user_and_file_hash(user_id: str, file_hash: str) -> ReceiptLookupResult | None`
- Produces: `receipt.services.create_receipt_with_file_hash(receipt_data: ReceiptData, file_hash: str) -> ReceiptLookupResult`
- Produces: `receipt.dataclasses.ReceiptLookupResult`

- [ ] **Step 1: Write failing receipt service tests**

Add tests to `receipt/tests.py`:

```python
import hashlib
import os
import tempfile
from decimal import Decimal

from django.test import TestCase

from receipt import services as receipt_services
from receipt.dataclasses import ReceiptData
from receipt.models import Receipt


class ReceiptFileHashTests(TestCase):
    def test_compute_file_sha256_hashes_file_bytes(self):
        content = b"same receipt bytes"
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            temp_file.write(content)
            temp_file_path = temp_file.name
        self.addCleanup(lambda: os.path.exists(temp_file_path) and os.unlink(temp_file_path))

        digest = receipt_services.compute_file_sha256(temp_file_path)

        self.assertEqual(digest, hashlib.sha256(content).hexdigest())

    def test_same_user_and_hash_reuses_receipt(self):
        receipt_data = ReceiptData(user_id="user-a", image_url="media/uploads/a.jpg", status="pending")

        created = receipt_services.create_receipt_with_file_hash(receipt_data, "a" * 64)
        reused = receipt_services.create_receipt_with_file_hash(receipt_data, "a" * 64)

        self.assertTrue(created.created)
        self.assertFalse(reused.created)
        self.assertEqual(created.receipt_id, reused.receipt_id)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_different_users_can_use_same_hash(self):
        first = receipt_services.create_receipt_with_file_hash(
            ReceiptData(user_id="user-a", image_url="media/uploads/a.jpg", status="pending"),
            "b" * 64,
        )
        second = receipt_services.create_receipt_with_file_hash(
            ReceiptData(user_id="user-b", image_url="media/uploads/b.jpg", status="pending"),
            "b" * 64,
        )

        self.assertTrue(first.created)
        self.assertTrue(second.created)
        self.assertNotEqual(first.receipt_id, second.receipt_id)
        self.assertEqual(Receipt.objects.count(), 2)

    def test_lookup_returns_receipt_status_and_image_url(self):
        created = receipt_services.create_receipt_with_file_hash(
            ReceiptData(
                user_id="user-a",
                image_url="media/uploads/a.jpg",
                status="completed",
                total_amount=Decimal("10.00"),
            ),
            "c" * 64,
        )

        result = receipt_services.get_receipt_by_user_and_file_hash("user-a", "c" * 64)

        self.assertIsNotNone(result)
        self.assertEqual(result.receipt_id, created.receipt_id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.image_url, "media/uploads/a.jpg")
        self.assertFalse(result.created)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptFileHashTests`

Expected: FAIL because `compute_file_sha256` and `create_receipt_with_file_hash` do not exist.

- [ ] **Step 3: Add model, dataclass, migration, and service implementation**

Implement:

```python
@dataclass
class ReceiptLookupResult:
    receipt_id: str
    user_id: str
    image_url: str
    status: str
    created: bool
    file_hash: Optional[str] = None
```

Add `file_hash` to `Receipt`, add the conditional unique constraint, stream file hashing in 1 MB chunks, and create receipts inside `transaction.atomic()` with `IntegrityError` fallback to lookup.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptFileHashTests`

Expected: PASS.

---

### Task 2: Telegram Duplicate Upload Flow

**Files:**
- Modify: `telegram_bot/process_message.py`
- Modify: `receipt/tests.py`

**Interfaces:**
- Consumes: `ReceiptLookupResult.status`
- Consumes: `receipt_services.compute_file_sha256(file_path)`
- Consumes: `receipt_services.get_receipt_by_user_and_file_hash(user_id, file_hash)`
- Consumes: `receipt_services.create_receipt_with_file_hash(receipt_data, file_hash)`
- Produces: `telegram_bot.process_message.get_receipt_duplicate_action(status: str) -> str`

- [ ] **Step 1: Write failing duplicate action tests**

Add tests to `receipt/tests.py`:

```python
class ReceiptDuplicateActionTests(TestCase):
    def test_completed_duplicate_skips_processing(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("completed"), "skip_completed")

    def test_pending_or_processing_duplicate_skips_new_task(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("pending"), "skip_in_progress")
        self.assertEqual(get_receipt_duplicate_action("processing"), "skip_in_progress")

    def test_failed_duplicate_retries_same_receipt(self):
        from telegram_bot.process_message import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("failed"), "retry")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test receipt.tests.ReceiptDuplicateActionTests`

Expected: FAIL because `get_receipt_duplicate_action` does not exist.

- [ ] **Step 3: Implement Telegram duplicate branching**

Add:

```python
def get_receipt_duplicate_action(status: str) -> str:
    if status == "completed":
        return "skip_completed"
    if status in {"pending", "processing"}:
        return "skip_in_progress"
    if status == "failed":
        return "retry"
    return "retry"
```

Update `process_receipt_upload` to compute the hash before upload, skip completed/pending/processing duplicates, and retry failed duplicates on the same receipt ID.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test receipt.tests.ReceiptDuplicateActionTests`

Expected: PASS.

---

### Task 3: Extraction Failure Status Regression

**Files:**
- Modify: `extract_info/tests.py`
- Modify: `extract_info/tasks.py`

**Interfaces:**
- Produces: `extract_info.tasks.mark_receipt_failed(receipt_id: str, bot_token: str | None = None, chat_id: int | None = None) -> None`

- [ ] **Step 1: Write failing task status test**

Add to `extract_info/tests.py`:

```python
from unittest.mock import patch

from django.test import TestCase

from extract_info.services import normalize_store_name


class ReceiptTaskFailureTests(TestCase):
    @patch("extract_info.tasks.receipt_services.update_receipt")
    def test_mark_receipt_failed_uses_valid_failed_status(self, update_receipt):
        from extract_info.tasks import mark_receipt_failed

        mark_receipt_failed("receipt-id", bot_token=None, chat_id=None)

        update_receipt.assert_called_once_with("receipt-id", status="failed")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python manage.py test extract_info.tests.ReceiptTaskFailureTests`

Expected: FAIL because `mark_receipt_failed` does not exist.

- [ ] **Step 3: Implement failure helper and task call**

Move exhausted retry failure update into `mark_receipt_failed(...)` and call it from `process_file_task` when `self.request.retries >= self.max_retries`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python manage.py test extract_info.tests.ReceiptTaskFailureTests`

Expected: PASS.

---

### Task 4: Full Verification and Commit

**Files:**
- Verify all modified files.

**Interfaces:**
- Consumes all previous tasks.
- Produces a verified implementation commit.

- [ ] **Step 1: Run focused tests**

Run:

```bash
python manage.py test receipt.tests.ReceiptFileHashTests receipt.tests.ReceiptDuplicateActionTests extract_info.tests.ReceiptTaskFailureTests
```

Expected: PASS.

- [ ] **Step 2: Run full test suite**

Run: `python manage.py test`

Expected: PASS, unless the local PostgreSQL test database is unavailable. If unavailable, record the exact error.

- [ ] **Step 3: Inspect git diff**

Run: `git diff --stat`

Expected: only duplicate receipt implementation, migration, tests, and this plan changed.

- [ ] **Step 4: Commit implementation**

Run:

```bash
git add docs/superpowers/plans/2026-07-17-duplicate-receipts.md receipt telegram_bot/process_message.py extract_info
git commit -m "feat: prevent duplicate receipt uploads"
```
