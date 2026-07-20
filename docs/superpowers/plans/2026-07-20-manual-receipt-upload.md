# Manual Receipt Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a staff Django upload UI for receipt images and PDF bank statements, backed by shared receipt-upload orchestration that Telegram also uses.

**Architecture:** Put upload lifecycle business rules in `receipt.services.prepare_receipt_upload()`. Keep browser form handling in `receipt.views`, Telegram download and replies in `telegram_bot.process_message`, and extraction in the existing Celery task.

**Tech Stack:** Django 6.0, Django test runner, Celery, python-telegram-bot, local upload service in `handle_files.services.upload`, PostgreSQL-backed receipt models.

## Global Constraints

- Do not implement bank statement extraction; PDFs must continue through `process_file_task(file_type='pdf')` and `extract_bank_statement_text()`.
- Do not add receipt model fields or migrations.
- Keep duplicate detection based on per-user SHA-256 file hashes.
- Staff-only browser upload; no public upload endpoint.
- Keep Telegram authentication and messaging in `telegram_bot.process_message`.
- Keep Django UI form, redirects, and flash messages in `receipt.views` and receipt templates.
- Use TDD for business logic and behavior changes.

---

## File Structure

- `receipt/dataclasses.py`: add DTOs for shared upload requests and results.
- `receipt/services.py`: add duplicate action helper and `prepare_receipt_upload()` orchestration.
- `receipt/forms.py`: create the manual upload form and file-type inference.
- `receipt/views.py`: add the staff upload view and keep review views unchanged.
- `receipt/urls.py`: add the upload route.
- `receipt/templates/receipt/upload.html`: create the staff upload page.
- `templates/base.html`: add a staff navigation link to the upload page.
- `receipt/tests.py`: add shared service tests and upload view tests.
- `telegram_bot/process_message.py`: refactor receipt upload to delegate to the shared service.
- `extract_info/tasks.py`: allow `chat_id=None` for browser uploads.

---

### Task 1: Shared Receipt Upload Service

**Files:**
- Modify: `receipt/dataclasses.py`
- Modify: `receipt/services.py`
- Modify: `receipt/tests.py`

**Interfaces:**
- Consumes: `ReceiptData`, `create_receipt_with_file_hash()`, `get_receipt_by_user_and_file_hash()`, `update_receipt()`, `compute_file_sha256()`
- Produces: `ReceiptUploadRequest`, `ReceiptUploadResult`, `receipt.services.get_receipt_duplicate_action(status: str) -> str`, `receipt.services.prepare_receipt_upload(request: ReceiptUploadRequest, upload_service: UploadService | None = None) -> ReceiptUploadResult`

- [ ] **Step 1: Write failing shared-service tests**

Add these imports near the top of `receipt/tests.py`:

```python
from pathlib import Path
```

Add this test class after `ReceiptDuplicateActionTests` in `receipt/tests.py`:

```python
class ReceiptUploadPreparationTests(TestCase):
    class DummyUploadService:
        def __init__(self):
            self.uploads = []

        def upload_file(self, file_path, object_name):
            self.uploads.append((file_path, object_name))
            return f"media/uploads/{object_name}"

    def write_upload_file(self, content=b"receipt bytes", suffix=".jpg"):
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_file.write(content)
        temp_file.close()
        self.addCleanup(lambda: os.path.exists(temp_file.name) and os.unlink(temp_file.name))
        return temp_file.name

    def upload_request(self, *, user_id="manual-user", content=b"receipt bytes", filename="receipt.jpg", file_type="image"):
        from receipt.dataclasses import ReceiptUploadRequest

        return ReceiptUploadRequest(
            user_id=user_id,
            source_file_path=self.write_upload_file(content=content, suffix=Path(filename).suffix),
            original_filename=filename,
            file_type=file_type,
        )

    def test_new_image_upload_creates_pending_receipt_and_should_enqueue(self):
        request = self.upload_request(filename="receipt.jpg", file_type="image")
        upload_service = self.DummyUploadService()

        result = receipt_services.prepare_receipt_upload(request, upload_service=upload_service)

        receipt = Receipt.objects.get(receipt_id=result.receipt_id)
        self.assertEqual(result.action, "created")
        self.assertEqual(result.status, "pending")
        self.assertEqual(result.file_type, "image")
        self.assertTrue(result.should_enqueue)
        self.assertEqual(receipt.status, "pending")
        self.assertEqual(receipt.file_hash, result.file_hash)
        self.assertTrue(result.image_url.startswith("media/uploads/"))
        self.assertEqual(len(upload_service.uploads), 1)
        self.assertEqual(Path(upload_service.uploads[0][1]).suffix, ".jpg")

    def test_new_pdf_upload_preserves_pdf_file_type_for_processing(self):
        request = self.upload_request(content=b"%PDF-1.4", filename="statement.pdf", file_type="pdf")
        upload_service = self.DummyUploadService()

        result = receipt_services.prepare_receipt_upload(request, upload_service=upload_service)

        self.assertEqual(result.action, "created")
        self.assertEqual(result.file_type, "pdf")
        self.assertTrue(result.should_enqueue)
        self.assertEqual(Path(upload_service.uploads[0][1]).suffix, ".pdf")

    def test_completed_duplicate_skips_upload_and_enqueue(self):
        first = receipt_services.prepare_receipt_upload(
            self.upload_request(content=b"same bytes"),
            upload_service=self.DummyUploadService(),
        )
        receipt_services.update_receipt(first.receipt_id, status="completed")
        duplicate_service = self.DummyUploadService()

        result = receipt_services.prepare_receipt_upload(
            self.upload_request(content=b"same bytes"),
            upload_service=duplicate_service,
        )

        self.assertEqual(result.receipt_id, first.receipt_id)
        self.assertEqual(result.action, "skip_completed")
        self.assertEqual(result.status, "completed")
        self.assertFalse(result.should_enqueue)
        self.assertEqual(duplicate_service.uploads, [])

    def test_in_progress_duplicates_skip_enqueue(self):
        for status in ("pending", "processing", "needs_review"):
            with self.subTest(status=status):
                request = self.upload_request(user_id=f"user-{status}", content=f"bytes-{status}".encode())
                first = receipt_services.prepare_receipt_upload(request, upload_service=self.DummyUploadService())
                receipt_services.update_receipt(first.receipt_id, status=status)
                duplicate_service = self.DummyUploadService()

                result = receipt_services.prepare_receipt_upload(
                    self.upload_request(user_id=f"user-{status}", content=f"bytes-{status}".encode()),
                    upload_service=duplicate_service,
                )

                self.assertEqual(result.receipt_id, first.receipt_id)
                self.assertIn(result.action, {"skip_in_progress", "skip_needs_review"})
                self.assertEqual(result.status, status)
                self.assertFalse(result.should_enqueue)
                self.assertEqual(duplicate_service.uploads, [])

    def test_failed_duplicate_retries_existing_receipt(self):
        first = receipt_services.prepare_receipt_upload(
            self.upload_request(content=b"retry bytes"),
            upload_service=self.DummyUploadService(),
        )
        original_image_url = first.image_url
        receipt_services.update_receipt(first.receipt_id, status="failed")
        retry_service = self.DummyUploadService()

        result = receipt_services.prepare_receipt_upload(
            self.upload_request(content=b"retry bytes", filename="retry.png"),
            upload_service=retry_service,
        )

        receipt = Receipt.objects.get(receipt_id=first.receipt_id)
        self.assertEqual(result.receipt_id, first.receipt_id)
        self.assertEqual(result.action, "retry")
        self.assertEqual(result.status, "pending")
        self.assertTrue(result.should_enqueue)
        self.assertEqual(receipt.status, "pending")
        self.assertNotEqual(receipt.image_url, original_image_url)
        self.assertEqual(len(retry_service.uploads), 1)
        self.assertEqual(Path(retry_service.uploads[0][1]).suffix, ".png")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python manage.py test receipt.tests.ReceiptUploadPreparationTests
```

Expected: FAIL with an import or attribute error because `ReceiptUploadRequest` and `prepare_receipt_upload()` do not exist.

- [ ] **Step 3: Add upload DTOs**

Append these dataclasses to `receipt/dataclasses.py`:

```python
@dataclass(frozen=True)
class ReceiptUploadRequest:
    user_id: str
    source_file_path: str
    original_filename: str
    file_type: str


@dataclass(frozen=True)
class ReceiptUploadResult:
    receipt_id: str
    user_id: str
    image_url: str
    status: str
    action: str
    file_hash: str
    file_type: str
    should_enqueue: bool
```

- [ ] **Step 4: Add shared upload orchestration**

Update imports in `receipt/services.py`:

```python
import hashlib
import uuid
from pathlib import Path

from handle_files.services.upload import UploadServiceFactory

from .models import Receipt, ReceiptItem
from typing import List, Optional
from decimal import Decimal
from django.db import IntegrityError, transaction
from django.utils import timezone
from .dataclasses import ReceiptData, ReceiptLookupResult, ReceiptUploadRequest, ReceiptUploadResult
from pgvector.django import CosineDistance
```

Add these helpers in `receipt/services.py` after `compute_file_sha256()`:

```python
def get_receipt_duplicate_action(status: str) -> str:
    if status == "completed":
        return "skip_completed"
    if status in {"pending", "processing"}:
        return "skip_in_progress"
    if status == "needs_review":
        return "skip_needs_review"
    if status == "failed":
        return "retry"
    return "retry"


def prepare_receipt_upload(request: ReceiptUploadRequest, upload_service=None) -> ReceiptUploadResult:
    if request.file_type not in {"image", "pdf"}:
        raise ValueError(f"Unsupported receipt upload file type: {request.file_type}")

    file_hash = compute_file_sha256(request.source_file_path)
    existing_receipt = get_receipt_by_user_and_file_hash(request.user_id, file_hash)
    if existing_receipt:
        action = get_receipt_duplicate_action(existing_receipt.status)
        if action != "retry":
            return _receipt_upload_result(existing_receipt, action, request.file_type, should_enqueue=False)
        return _retry_receipt_upload(request, existing_receipt, file_hash, upload_service)

    uploaded_url = _upload_receipt_source_file(request, upload_service)
    created_receipt = create_receipt_with_file_hash(
        ReceiptData(
            user_id=request.user_id,
            image_url=uploaded_url,
            status="pending",
        ),
        file_hash,
    )
    if created_receipt.created:
        return _receipt_upload_result(created_receipt, "created", request.file_type, should_enqueue=True)

    action = get_receipt_duplicate_action(created_receipt.status)
    if action != "retry":
        return _receipt_upload_result(created_receipt, action, request.file_type, should_enqueue=False)
    return _retry_receipt_upload(request, created_receipt, file_hash, upload_service)


def _retry_receipt_upload(request: ReceiptUploadRequest, receipt: ReceiptLookupResult, file_hash: str, upload_service=None) -> ReceiptUploadResult:
    uploaded_url = _upload_receipt_source_file(request, upload_service)
    update_receipt(receipt.receipt_id, image_url=uploaded_url, status="pending")
    return ReceiptUploadResult(
        receipt_id=receipt.receipt_id,
        user_id=receipt.user_id,
        image_url=uploaded_url,
        status="pending",
        action="retry",
        file_hash=file_hash,
        file_type=request.file_type,
        should_enqueue=True,
    )


def _receipt_upload_result(
    receipt: ReceiptLookupResult,
    action: str,
    file_type: str,
    *,
    should_enqueue: bool,
) -> ReceiptUploadResult:
    return ReceiptUploadResult(
        receipt_id=receipt.receipt_id,
        user_id=receipt.user_id,
        image_url=receipt.image_url,
        status=receipt.status,
        action=action,
        file_hash=receipt.file_hash or "",
        file_type=file_type,
        should_enqueue=should_enqueue,
    )


def _upload_receipt_source_file(request: ReceiptUploadRequest, upload_service=None) -> str:
    service = upload_service or UploadServiceFactory.create("local")
    return service.upload_file(request.source_file_path, _receipt_upload_object_name(request.original_filename))


def _receipt_upload_object_name(original_filename: str) -> str:
    suffix = Path(original_filename or "").suffix.lower()
    if not suffix:
        suffix = ".bin"
    return f"{uuid.uuid4().hex}{suffix}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
python manage.py test receipt.tests.ReceiptUploadPreparationTests receipt.tests.ReceiptFileHashTests
```

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

```bash
git add receipt/dataclasses.py receipt/services.py receipt/tests.py
git commit -m "feat: add shared receipt upload preparation"
```

---

### Task 2: Browser Upload Form And Staff View

**Files:**
- Create: `receipt/forms.py`
- Create: `receipt/templates/receipt/upload.html`
- Modify: `receipt/views.py`
- Modify: `receipt/urls.py`
- Modify: `templates/base.html`
- Modify: `extract_info/tasks.py`
- Modify: `receipt/tests.py`

**Interfaces:**
- Consumes: `ReceiptUploadRequest`, `ReceiptUploadResult`, `receipt_services.prepare_receipt_upload()`, `process_file_task.delay(receipt_id: str, file_path: str, chat_id: int | None, file_type: str)`
- Produces: `ReceiptUploadForm`, `receipt.views.upload`, `receipt-review:upload`

- [ ] **Step 1: Write failing upload view tests**

Add these imports near the top of `receipt/tests.py`:

```python
from django.core.files.uploadedfile import SimpleUploadedFile
```

Add this class after `ReceiptReviewViewTests` in `receipt/tests.py`:

```python
class ReceiptManualUploadViewTests(TestCase):
    def create_staff_user(self):
        return get_user_model().objects.create_user(
            username="staff-uploader",
            password="password",
            is_staff=True,
        )

    def create_regular_user(self):
        return get_user_model().objects.create_user(
            username="regular-uploader",
            password="password",
            is_staff=False,
        )

    def upload_url(self):
        return reverse("receipt-review:upload")

    def service_result(self, *, file_type="image", action="created", should_enqueue=True, status="pending"):
        from receipt.dataclasses import ReceiptUploadResult

        return ReceiptUploadResult(
            receipt_id="00000000-0000-0000-0000-000000000001",
            user_id="staff-uploader",
            image_url="media/uploads/source.jpg" if file_type == "image" else "media/uploads/source.pdf",
            status=status,
            action=action,
            file_hash="a" * 64,
            file_type=file_type,
            should_enqueue=should_enqueue,
        )

    def test_upload_page_requires_staff(self):
        self.client.force_login(self.create_regular_user())

        response = self.client.get(self.upload_url())

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    @patch("receipt.views.process_file_task.delay")
    @patch("receipt.views.receipt_services.prepare_receipt_upload")
    def test_staff_image_upload_enqueues_processing(self, prepare_receipt_upload, delay):
        prepare_receipt_upload.return_value = self.service_result(file_type="image")
        self.client.force_login(self.create_staff_user())
        uploaded_file = SimpleUploadedFile("receipt.jpg", b"receipt bytes", content_type="image/jpeg")

        response = self.client.post(self.upload_url(), {"document": uploaded_file})

        self.assertRedirects(response, self.upload_url())
        request = prepare_receipt_upload.call_args.args[0]
        self.assertEqual(request.user_id, "staff-uploader")
        self.assertEqual(request.original_filename, "receipt.jpg")
        self.assertEqual(request.file_type, "image")
        delay.assert_called_once_with(
            receipt_id="00000000-0000-0000-0000-000000000001",
            file_path="media/uploads/source.jpg",
            chat_id=None,
            file_type="image",
        )

    @patch("receipt.views.process_file_task.delay")
    @patch("receipt.views.receipt_services.prepare_receipt_upload")
    def test_staff_pdf_upload_enqueues_pdf_processing(self, prepare_receipt_upload, delay):
        prepare_receipt_upload.return_value = self.service_result(file_type="pdf")
        self.client.force_login(self.create_staff_user())
        uploaded_file = SimpleUploadedFile("statement.pdf", b"%PDF-1.4", content_type="application/pdf")

        response = self.client.post(self.upload_url(), {"document": uploaded_file})

        self.assertRedirects(response, self.upload_url())
        request = prepare_receipt_upload.call_args.args[0]
        self.assertEqual(request.original_filename, "statement.pdf")
        self.assertEqual(request.file_type, "pdf")
        delay.assert_called_once_with(
            receipt_id="00000000-0000-0000-0000-000000000001",
            file_path="media/uploads/source.pdf",
            chat_id=None,
            file_type="pdf",
        )

    @patch("receipt.views.process_file_task.delay")
    @patch("receipt.views.receipt_services.prepare_receipt_upload")
    def test_completed_duplicate_upload_does_not_enqueue_processing(self, prepare_receipt_upload, delay):
        prepare_receipt_upload.return_value = self.service_result(
            action="skip_completed",
            should_enqueue=False,
            status="completed",
        )
        self.client.force_login(self.create_staff_user())
        uploaded_file = SimpleUploadedFile("receipt.jpg", b"receipt bytes", content_type="image/jpeg")

        response = self.client.post(self.upload_url(), {"document": uploaded_file}, follow=True)

        self.assertEqual(response.status_code, 200)
        delay.assert_not_called()
        self.assertContains(response, "already exists")

    def test_invalid_upload_type_renders_form_error(self):
        self.client.force_login(self.create_staff_user())
        uploaded_file = SimpleUploadedFile("notes.txt", b"not a receipt", content_type="text/plain")

        response = self.client.post(self.upload_url(), {"document": uploaded_file})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a receipt image or PDF bank statement.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python manage.py test receipt.tests.ReceiptManualUploadViewTests
```

Expected: FAIL because `receipt-review:upload`, `receipt.forms`, and `receipt.views.upload` do not exist.

- [ ] **Step 3: Add the upload form**

Create `receipt/forms.py`:

```python
from pathlib import Path

from django import forms


class ReceiptUploadForm(forms.Form):
    document = forms.FileField(
        label="Receipt or statement file",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/*,application/pdf",
                "class": "form-control",
            }
        ),
    )

    def clean_document(self):
        document = self.cleaned_data["document"]
        file_type = infer_receipt_upload_file_type(
            document.name,
            getattr(document, "content_type", ""),
        )
        if file_type is None:
            raise forms.ValidationError("Upload a receipt image or PDF bank statement.")
        self.cleaned_data["file_type"] = file_type
        return document


def infer_receipt_upload_file_type(filename: str, content_type: str | None) -> str | None:
    suffix = Path(filename or "").suffix.lower()
    normalized_content_type = (content_type or "").lower()

    if normalized_content_type == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if normalized_content_type.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    return None
```

- [ ] **Step 4: Add the upload view**

Update imports in `receipt/views.py`:

```python
import logging
import mimetypes
import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from extract_info.tasks import process_file_task
from receipt import extraction_review
from receipt import services as receipt_services
from receipt.dataclasses import ReceiptUploadRequest
from receipt.forms import ReceiptUploadForm
from receipt.models import Category, Receipt, ReceiptExtractionReview
```

Add a module logger after imports:

```python
logger = logging.getLogger(__name__)
```

Add this view before `review_queue()`:

```python
@staff_member_required
def upload(request):
    if request.method == "POST":
        form = ReceiptUploadForm(request.POST, request.FILES)
        if form.is_valid():
            temp_file_path = None
            uploaded_file = form.cleaned_data["document"]
            try:
                temp_file_path = _write_uploaded_receipt_file(uploaded_file)
                result = receipt_services.prepare_receipt_upload(
                    ReceiptUploadRequest(
                        user_id=_receipt_upload_user_id(request.user),
                        source_file_path=temp_file_path,
                        original_filename=uploaded_file.name,
                        file_type=form.cleaned_data["file_type"],
                    )
                )
                if result.should_enqueue:
                    process_file_task.delay(
                        receipt_id=result.receipt_id,
                        file_path=result.image_url,
                        chat_id=None,
                        file_type=result.file_type,
                    )
                    messages.success(
                        request,
                        f"Receipt {result.receipt_id} uploaded successfully and queued for processing.",
                    )
                else:
                    messages.info(
                        request,
                        f"Receipt {result.receipt_id} already exists with status {result.status}.",
                    )
                return redirect(reverse("receipt-review:upload"))
            except Exception:
                logger.exception("Manual receipt upload failed")
                form.add_error("document", "Could not upload the file. Please try again.")
            finally:
                if temp_file_path and os.path.exists(temp_file_path):
                    try:
                        os.unlink(temp_file_path)
                    except OSError:
                        logger.warning("Failed to remove temporary upload file %s", temp_file_path)
    else:
        form = ReceiptUploadForm()

    return render(request, "receipt/upload.html", {"form": form})


def _write_uploaded_receipt_file(uploaded_file):
    suffix = Path(uploaded_file.name or "").suffix.lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        for chunk in uploaded_file.chunks():
            temp_file.write(chunk)
        return temp_file.name


def _receipt_upload_user_id(user):
    return user.get_username() or str(user.pk)
```

- [ ] **Step 5: Add the upload URL**

Update `receipt/urls.py`:

```python
from django.urls import path

from receipt import views

app_name = "receipt-review"

urlpatterns = [
    path("upload/", views.upload, name="upload"),
    path("", views.review_queue, name="queue"),
    path("<uuid:receipt_id>/source/", views.review_source, name="source"),
    path("<uuid:receipt_id>/", views.review_detail, name="detail"),
]
```

- [ ] **Step 6: Add the upload template**

Create `receipt/templates/receipt/upload.html`:

```django
{% extends "base.html" %}
{% load static %}

{% block title %}Upload receipt{% endblock %}
{% block upload_nav_class %}active{% endblock %}
{% block styles %}<link rel="stylesheet" href="{% static 'receipt/review.css' %}">{% endblock %}

{% block content %}
<div class="d-flex flex-column flex-lg-row align-items-lg-end justify-content-between gap-3 mb-4">
  <div>
    <p class="eyebrow mb-2">Manual upload</p>
    <h1 class="display-6 fw-bold mb-2">Upload receipt</h1>
    <p class="text-secondary mb-0">Add a receipt image or PDF statement from this device.</p>
  </div>
  <a class="btn btn-outline-dark" href="{% url 'receipt-review:queue' %}">Review queue</a>
</div>

{% if messages %}
  {% for message in messages %}
    <div class="alert alert-{{ message.tags|default:'info' }}" role="alert">{{ message }}</div>
  {% endfor %}
{% endif %}

<section class="card review-card border-0">
  <div class="card-body p-3 p-lg-4">
    <form method="post" enctype="multipart/form-data" class="row g-3 align-items-end">
      {% csrf_token %}
      <div class="col-12 col-lg-8">
        <label class="form-label small fw-semibold" for="{{ form.document.id_for_label }}">File</label>
        {{ form.document }}
        {% if form.document.errors %}
          <div class="invalid-feedback d-block">{{ form.document.errors|striptags }}</div>
        {% endif %}
      </div>
      <div class="col-12 col-lg-4">
        <button class="btn btn-dark w-100" type="submit">Upload</button>
      </div>
    </form>
  </div>
</section>
{% endblock %}
```

- [ ] **Step 7: Add staff navigation**

Modify the staff block in `templates/base.html`:

```django
          {% if request.user.is_staff %}
            <li class="nav-item">
              <a class="nav-link {% block upload_nav_class %}{% endblock %}" href="{% url 'receipt-review:upload' %}">Upload</a>
            </li>
            <li class="nav-item">
              <a class="nav-link {% block review_nav_class %}{% endblock %}" href="{% url 'receipt-review:queue' %}">Review</a>
            </li>
          {% endif %}
```

- [ ] **Step 8: Make Celery task chat ID optional**

Change the task signature in `extract_info/tasks.py`:

```python
def process_file_task(self, receipt_id: str, file_path: str, chat_id: int | None = None, file_type: str = "image"):
```

- [ ] **Step 9: Run upload view tests**

Run:

```bash
python manage.py test receipt.tests.ReceiptManualUploadViewTests
```

Expected: PASS.

- [ ] **Step 10: Commit Task 2**

```bash
git add receipt/forms.py receipt/templates/receipt/upload.html receipt/views.py receipt/urls.py templates/base.html extract_info/tasks.py receipt/tests.py
git commit -m "feat: add manual receipt upload view"
```

---

### Task 3: Refactor Telegram Receipt Upload To Shared Service

**Files:**
- Modify: `telegram_bot/process_message.py`
- Modify: `receipt/tests.py`

**Interfaces:**
- Consumes: `ReceiptUploadRequest`, `ReceiptUploadResult`, `receipt_services.prepare_receipt_upload()`, `receipt_services.get_receipt_duplicate_action()`
- Produces: Telegram receipt uploads delegate duplicate and pending/retry decisions to the receipt service

- [ ] **Step 1: Move duplicate action tests to service import**

Replace the imports inside `ReceiptDuplicateActionTests` in `receipt/tests.py` so each test imports from `receipt.services`:

```python
    def test_completed_duplicate_skips_processing(self):
        from receipt.services import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("completed"), "skip_completed")

    def test_pending_or_processing_duplicate_skips_new_task(self):
        from receipt.services import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("pending"), "skip_in_progress")
        self.assertEqual(get_receipt_duplicate_action("processing"), "skip_in_progress")

    def test_failed_duplicate_retries_same_receipt(self):
        from receipt.services import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("failed"), "retry")

    def test_needs_review_duplicate_skips_new_task(self):
        from receipt.services import get_receipt_duplicate_action

        self.assertEqual(get_receipt_duplicate_action("needs_review"), "skip_needs_review")
```

- [ ] **Step 2: Add a failing Telegram delegation test**

Add this test class after `ReceiptDuplicateActionTests` in `receipt/tests.py`:

```python
class TelegramReceiptUploadDelegationTests(TestCase):
    @patch("telegram_bot.process_message.process_file_task.delay")
    @patch("telegram_bot.process_message.receipt_services.prepare_receipt_upload")
    @patch("telegram_bot.process_message.authenticate_user", new_callable=AsyncMock)
    def test_photo_upload_delegates_to_shared_receipt_service(
        self,
        authenticate_user,
        prepare_receipt_upload,
        delay,
    ):
        from receipt.dataclasses import ReceiptUploadResult
        from telegram_bot.process_message import process_receipt_upload

        authenticate_user.return_value = True
        prepare_receipt_upload.return_value = ReceiptUploadResult(
            receipt_id="00000000-0000-0000-0000-000000000002",
            user_id="telegram-user",
            image_url="media/uploads/telegram.jpg",
            status="pending",
            action="created",
            file_hash="b" * 64,
            file_type="image",
            should_enqueue=True,
        )
        document_file = SimpleNamespace(
            download_to_memory=AsyncMock(side_effect=lambda out: out.write(b"telegram receipt bytes"))
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(
                get_file=AsyncMock(return_value=document_file),
            )
        )
        update = SimpleNamespace(
            message=SimpleNamespace(
                photo=[SimpleNamespace(file_id="photo-file-id")],
                document=None,
                reply_text=AsyncMock(),
                from_user=SimpleNamespace(username="telegram-user", first_name="Roberto", id=42),
                chat_id=123,
            ),
            effective_chat=SimpleNamespace(id=123),
        )

        asyncio.run(process_receipt_upload(update, context))

        request = prepare_receipt_upload.call_args.args[0]
        self.assertEqual(request.user_id, "telegram-user")
        self.assertEqual(request.original_filename, "photo-file-id.jpg")
        self.assertEqual(request.file_type, "image")
        delay.assert_called_once_with(
            receipt_id="00000000-0000-0000-0000-000000000002",
            file_path="media/uploads/telegram.jpg",
            chat_id=123,
            file_type="image",
        )
```

- [ ] **Step 3: Run tests to verify delegation test fails**

Run:

```bash
python manage.py test receipt.tests.ReceiptDuplicateActionTests receipt.tests.TelegramReceiptUploadDelegationTests
```

Expected: FAIL because `process_receipt_upload()` still performs duplicate and upload orchestration inline.

- [ ] **Step 4: Refactor Telegram receipt upload**

Update imports in `telegram_bot/process_message.py`:

```python
from pathlib import Path
```

Change the receipt dataclass import:

```python
from receipt.dataclasses import ReceiptData, ReceiptItem as ReceiptItemData, ReceiptUploadRequest
```

Remove the `get_receipt_duplicate_action()` function from `telegram_bot/process_message.py`.

In `reply_for_existing_receipt()`, keep the existing body unchanged.

Replace `process_receipt_upload()` with:

```python
async def process_receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process receipt image upload with shared receipt upload orchestration."""
    if not await authenticate_user(update, context):
        await update.message.reply_text("⛔You are not authorized to use this bot.")
        return

    receipt_id = None
    temp_file_path = None

    try:
        document_file, original_filename, file_suffix = await _telegram_receipt_file(update, context)
        if document_file is None:
            await update.message.reply_text("❌ No file found in the message.")
            return

        file_data = io.BytesIO()
        await document_file.download_to_memory(out=file_data)
        file_data.seek(0)

        with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as temp_file:
            temp_file.write(file_data.read())
            temp_file_path = temp_file.name

        result = await sync_to_async(receipt_services.prepare_receipt_upload)(
            ReceiptUploadRequest(
                user_id=get_receipt_user(update),
                source_file_path=temp_file_path,
                original_filename=original_filename,
                file_type="image",
            )
        )
        receipt_id = result.receipt_id

        if not result.should_enqueue:
            await reply_for_existing_receipt(update, result.receipt_id, result.status, result.action)
            return

        await update.message.reply_text(
            f"✅ Receipt uploaded successfully!\n\n"
            f"Receipt ID: `{result.receipt_id}`\n"
            f"Status: {result.status}\n\n"
            f"Processing receipt data...",
            parse_mode="Markdown"
        )

        chat_id = update.effective_chat.id if update.effective_chat else update.message.chat_id
        process_file_task.delay(
            receipt_id=result.receipt_id,
            file_path=result.image_url,
            chat_id=chat_id,
            file_type=result.file_type
        )
        logger.info(f"Handed off receipt {result.receipt_id} processing to Celery.")
    except Exception as e:
        logger.error(f"Error processing receipt: {e}", exc_info=True)
        if receipt_id:
            try:
                await sync_to_async(receipt_services.update_receipt)(receipt_id, status='failed')
            except Exception as update_error:
                logger.error(f"Failed to update receipt status: {update_error}")

        await update.message.reply_text(
            f"❌ Error initiating receipt processing: {str(e)}\n\n"
            f"Receipt ID: `{receipt_id if receipt_id else 'N/A'}`\n"
            f"Status: failed",
            parse_mode="Markdown"
        )
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except OSError as cleanup_error:
                logger.error(f"Failed to cleanup temp file {temp_file_path}: {cleanup_error}")
```

Add this helper below `process_receipt_upload()`:

```python
async def _telegram_receipt_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if message.photo:
        photo = message.photo[-1]
        return (
            await context.bot.get_file(photo.file_id),
            f"{photo.file_id}.jpg",
            ".jpg",
        )

    if message.document:
        document = message.document
        original_filename = document.file_name or f"{document.file_id}{_telegram_document_suffix(document)}"
        return (
            await context.bot.get_file(document.file_id),
            original_filename,
            Path(original_filename).suffix or _telegram_document_suffix(document),
        )

    return None, "", ".jpg"


def _telegram_document_suffix(document) -> str:
    if document.mime_type == "image/png":
        return ".png"
    if document.mime_type == "image/jpeg":
        return ".jpg"
    suffix = Path(document.file_name or "").suffix
    return suffix or ".jpg"
```

- [ ] **Step 5: Run Telegram refactor tests**

Run:

```bash
python manage.py test receipt.tests.ReceiptDuplicateActionTests receipt.tests.TelegramReceiptUploadDelegationTests
```

Expected: PASS.

- [ ] **Step 6: Commit Task 3**

```bash
git add telegram_bot/process_message.py receipt/tests.py
git commit -m "refactor: share receipt upload orchestration"
```

---

### Task 4: Final Verification

**Files:**
- No production edits unless verification exposes a defect.

**Interfaces:**
- Consumes: all interfaces from Tasks 1-3
- Produces: verified implementation

- [ ] **Step 1: Run focused receipt tests**

Run:

```bash
python manage.py test receipt.tests.ReceiptUploadPreparationTests receipt.tests.ReceiptManualUploadViewTests receipt.tests.ReceiptDuplicateActionTests receipt.tests.TelegramReceiptUploadDelegationTests
```

Expected: PASS.

- [ ] **Step 2: Run focused extraction task tests**

Run:

```bash
python manage.py test extract_info.tests.ProcessFileTaskReviewIntegrationTests extract_info.tests.ReceiptTaskFailureTests
```

Expected: PASS.

- [ ] **Step 3: Run full test suite**

Run:

```bash
python manage.py test
```

Expected: PASS. If the local PostgreSQL database is unavailable, record the connection failure and the focused commands that were attempted.

- [ ] **Step 4: Inspect changed files**

Run:

```bash
git status --short
git diff --stat
```

Expected: only files listed in this plan changed.

- [ ] **Step 5: Commit verification fixes if any were required**

If verification required fixes, commit them:

```bash
git add receipt extract_info telegram_bot templates
git commit -m "fix: stabilize manual receipt upload"
```

If no fixes were required, do not create an empty commit.
