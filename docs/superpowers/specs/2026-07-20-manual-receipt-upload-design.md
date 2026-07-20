# Manual Receipt Upload Design

## Goal

Add a staff-facing Django UI in the receipt module for uploading receipt images or PDF bank statements from the browser, and refactor the existing Telegram receipt upload path so both sources use the same receipt-ingestion business logic.

## Scope

- Add a receipt upload page in the `receipt` app.
- Accept local image uploads for tickets and PDF uploads for bank statements.
- Store uploaded files through the existing local upload service.
- Reuse duplicate detection based on per-user SHA-256 file hashes.
- Create new receipts with `pending` status.
- Reuse failed duplicate receipts by setting them back to `pending`.
- Skip new processing for duplicate receipts already in `pending`, `processing`, `needs_review`, or `completed`.
- Enqueue the existing `process_file_task` for uploads that should be processed.
- Keep PDF processing routed through `extract_bank_statement_text()` via the current `file_type='pdf'` task branch.
- Keep Telegram-specific authentication and messages in the Telegram layer.
- Keep Django UI-specific forms, redirects, and flash messages in the view layer.

## Non Goals

- Implement bank statement extraction.
- Add new receipt fields or change the receipt data model.
- Change the LLM prompt, category enrichment, validation, or review workflow.
- Build a public upload flow for non-staff users.
- Add a generic ingestion framework beyond the shared receipt upload orchestration needed now.
- Add client-side upload progress or async polling.

## Chosen Approach

Create a small source-agnostic upload orchestration layer in `receipt.services`. It will accept a local file path plus metadata, handle the receipt lifecycle decisions, and return a result object that tells the caller whether processing should be enqueued.

This keeps the hard rules in one place:

- file hashing
- duplicate lookup
- receipt creation
- failed duplicate retry
- processing skip decisions
- upload path assignment

The Telegram handler and Django view stay responsible only for source-specific work: getting a file onto disk, identifying the user, communicating the result, and calling Celery when the service returns `should_enqueue=True`.

## Alternatives Considered

### Duplicate the Telegram flow in the Django view

This would be the fastest path to a browser upload page, but it would copy duplicate handling and status transitions into a second controller. The next source would repeat the same problem.

### Introduce a larger ingestion framework

Adapters for Telegram, browser uploads, bank statements, receipts, audio, notifications, and task dispatch could make the architecture more uniform. That is more structure than this feature needs, and the current app can get the main benefit with a focused service boundary.

## Service API

Add upload orchestration types in `receipt.dataclasses`:

- `ReceiptUploadRequest`
- `ReceiptUploadResult`

Request fields:

- `user_id`: source-specific user identifier
- `source_file_path`: local path to the file that was uploaded or downloaded
- `original_filename`: original or source-derived file name
- `file_type`: `image` or `pdf`

Result fields:

- `receipt_id`
- `status`
- `action`: `created`, `retry`, `skip_completed`, `skip_in_progress`, or `skip_needs_review`
- `image_url`
- `file_hash`
- `file_type`
- `should_enqueue`

Add this service function:

```python
def prepare_receipt_upload(request: ReceiptUploadRequest) -> ReceiptUploadResult:
    ...
```

The function will:

1. Compute the SHA-256 file hash from `source_file_path`.
2. Look up an existing receipt by `user_id` and `file_hash`.
3. If the existing receipt is `completed`, `pending`, `processing`, or `needs_review`, return a skip result without uploading or enqueueing.
4. If the existing receipt is `failed`, upload the new file, update the existing receipt's `image_url` and `status='pending'`, and return a retry result with `should_enqueue=True`.
5. If no receipt exists, upload the file, create a receipt with `pending` status and `file_hash`, and return a created result with `should_enqueue=True`.
6. If create hits the database uniqueness race path and returns an existing receipt, apply the same duplicate action rules.

Move `get_receipt_duplicate_action()` from `telegram_bot.process_message` to `receipt.services` so it can be shared. Telegram will call the service function directly.

## Upload Storage

The upload orchestration service will use `UploadServiceFactory.create('local')`, matching the current Telegram receipt path. Object names will be generated as `<uuid><original-extension>` so uploads avoid path traversal and filename collisions while preserving the extension needed for later inspection.

Uploaded images and PDFs will continue to be referenced through `Receipt.image_url`, even for PDFs, because the current model uses this field as the source-file path.

## Django UI

Add a staff-only upload view under the existing receipt URL namespace. Use these routes:

- `GET /review/receipts/upload/`
- `POST /review/receipts/upload/`

The page should present a compact upload form with:

- file input accepting image MIME types and `application/pdf`
- submit button

The view will infer `file_type` automatically from the uploaded file's content type and extension. Images map to `image`; PDFs map to `pdf`.

On successful created or retry upload:

- enqueue `process_file_task.delay(receipt_id=..., file_path=image_url, chat_id=None, file_type=...)`
- show a success message with the receipt ID and pending status
- redirect back to the upload page

On duplicate skip:

- do not enqueue a task
- show an informational message with the existing receipt ID and status

On invalid input or upload failure:

- render the form with a validation error
- do not create a receipt if the file has not been stored successfully
- if a retry update fails after reusing a failed receipt, leave the existing receipt failed

Add a staff navigation entry for upload alongside the review queue.

## Telegram Flow

Refactor `process_receipt_upload()` so it:

1. Authenticates the Telegram user.
2. Downloads the photo or image document to a temporary local file.
3. Builds a `ReceiptUploadRequest`.
4. Calls the shared receipt upload service.
5. Replies using Telegram-specific wording based on the returned result.
6. Enqueues `process_file_task` only when `should_enqueue=True`.
7. Cleans up its temporary download file.

This preserves the current Telegram behavior while removing business rules from the handler.

PDF bank statements from Telegram will keep using the existing `process_bank_statement()` disabled response until that route is intentionally enabled. The new browser upload page will still enqueue PDFs through `process_file_task(file_type='pdf')`, which keeps calling `extract_bank_statement_text()`.

## Celery Task Behavior

No extraction behavior changes are required. The existing `process_file_task` already branches by `file_type`:

- `image` calls `extract_receipt_text()`
- `pdf` calls `extract_bank_statement_text()`
- `audio` calls `transcribe_and_extract_text()`

This feature only changes how `receipt_id`, `file_path`, and `file_type` are prepared before the task starts.

Because browser uploads do not have a Telegram chat, change the task signature to accept `chat_id: int | None = None`. The task already only sends Telegram messages when both bot token and chat ID are available.

## Error Handling

- Invalid file types should be rejected before calling the upload service.
- Duplicate completed, in-progress, or needs-review uploads should not replace `image_url`.
- Failed duplicate retries may replace `image_url` with the latest uploaded copy.
- Upload failures before receipt creation should not create a receipt.
- Task failures should continue to mark the receipt `failed` after retries are exhausted.
- Temporary request files should be cleaned up by the source adapter after the shared service returns or raises.

## Testing

Add focused tests around the shared business logic:

- new image upload creates a pending receipt and returns `should_enqueue=True`
- new PDF upload returns `file_type='pdf'` and `should_enqueue=True`
- completed duplicate returns a skip result and does not upload a new copy
- pending or processing duplicate returns a skip result and does not enqueue
- needs-review duplicate returns a skip result and does not enqueue
- failed duplicate updates the existing receipt to `pending` and returns `should_enqueue=True`

Add light view tests:

- upload page requires staff access
- valid image upload enqueues the task
- valid PDF upload enqueues the task with `file_type='pdf'`
- completed duplicate upload does not enqueue a task

Keep Telegram handler tests minimal unless needed for the refactor, because the core behavior should be protected in the receipt service tests.

## Verification

Verification should include:

- focused receipt service tests
- focused upload view tests
- existing duplicate receipt tests
- existing extraction task tests
- full Django test suite if the local database is available

Manual verification should include opening the upload page as staff, uploading an image, confirming the receipt enters `pending`, and confirming duplicate uploads do not enqueue duplicate work.
