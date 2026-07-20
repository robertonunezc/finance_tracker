# Process File Notification Boundary Design

## Goal

Remove client-specific messaging from `process_file_task` while preserving Telegram completion, review, and failure notifications through a separate notification path.

## Scope

- Persist the source of each receipt upload.
- Persist source metadata needed by source-specific clients, starting with Telegram `chat_id`.
- Keep receipt extraction, enrichment, validation, and persistence in `process_file_task`.
- Move Telegram `Bot` usage and message formatting out of `extract_info.tasks`.
- Notify Telegram users automatically after receipt processing succeeds, needs review, or finally fails.
- Keep manual browser uploads as no-op notifications for now.

## Non Goals

- Add live browser polling or push notifications.
- Introduce a full event outbox.
- Change extraction prompts, categorization, duplicate detection, or review logic.
- Change existing receipt statuses.

## Chosen Approach

Store source metadata directly on `Receipt` with `source_type` and `source_metadata`. Upload entry points set those fields when creating or retrying receipts. `process_file_task` no longer receives `chat_id`; it only processes the file and schedules a generic notification task after the receipt reaches a terminal user-visible state.

The notification task loads the receipt and dispatches by `source_type`. For `telegram`, it calls a Telegram notifier that owns `Bot`, token lookup, `chat_id`, and message formatting. For `manual_upload` and unknown sources, it logs and skips external delivery.

## Data Model

Add to `Receipt`:

- `source_type`: string, default `unknown`, with choices `unknown`, `telegram`, and `manual_upload`
- `source_metadata`: JSON object, default `{}` and blank allowed

Historical receipts will migrate to `unknown`. New Telegram uploads store `telegram`; new manual uploads store `manual_upload`.

## Task Responsibilities

`process_file_task(receipt_id, file_path, file_type='image')`:

1. Set the receipt status to `processing`.
2. Extract data using the existing `file_type` branch.
3. Enrich categories.
4. Apply extraction results through `receipt.extraction_review`.
5. Clean temporary files when allowed.
6. Schedule `notify_receipt_processed_task(receipt_id)`.
7. On exhausted retries, mark the receipt `failed` and schedule the same notification task.

`notify_receipt_processed_task(receipt_id)`:

1. Load the receipt with items and review data.
2. Dispatch by `source_type`.
3. Send Telegram messages only for Telegram receipts with a stored `chat_id`.
4. Skip manual and unknown receipts.

## Telegram Notifications

The Telegram notifier rebuilds the existing result messages from persisted data:

- completed: processed successfully, total, item count, item lines
- needs_review: manual review warning, total, issue count, item count, item lines
- failed: failed to process receipt

If `TELEGRAM_BOT_TOKEN` or `chat_id` is missing, it logs and skips delivery without failing receipt processing.

## Testing

Focused tests should cover:

- `process_file_task` no longer imports or calls Telegram and schedules the generic notification task.
- final retry failure marks the receipt failed and schedules notification.
- Telegram upload passes source metadata to the shared upload service and enqueues `process_file_task` without `chat_id`.
- manual upload passes manual source metadata and enqueues `process_file_task` without `chat_id`.
- notification task sends the existing Telegram completed, needs-review, and failed messages.
- manual upload notification is skipped.

## Verification

Run the focused Django tests for `extract_info`, `receipt`, and Telegram upload behavior. Run migrations check/tests if the local database is available.
