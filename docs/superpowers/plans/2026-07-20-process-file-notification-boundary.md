# Process File Notification Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple receipt processing from Telegram messaging while preserving automatic Telegram result notifications.

**Architecture:** Persist receipt source data on `Receipt`, remove `chat_id` and Telegram imports from `process_file_task`, and add a separate notification task that dispatches by receipt source. Telegram formatting and delivery live in `telegram_bot.notifications`; manual uploads currently skip external notification.

**Tech Stack:** Django 6.0, Celery, python-telegram-bot, Django test runner, PostgreSQL JSONField.

## Global Constraints

- `process_file_task` must not accept `chat_id`.
- `process_file_task` must not import or instantiate Telegram `Bot`.
- Telegram users must still receive completed, needs-review, and final-failure messages.
- Manual browser uploads must not try to send Telegram messages.
- Existing extraction, enrichment, duplicate detection, and review behavior must remain unchanged.
- Use TDD for behavior changes.

---

## File Structure

- `receipt/models.py`: add source choices and source fields on `Receipt`.
- `receipt/migrations/0010_receipt_source_metadata.py`: add database fields.
- `receipt/dataclasses.py`: carry source fields through upload request/result DTOs.
- `receipt/services.py`: persist source fields during create/retry and expose full lookup data.
- `receipt/tasks.py`: add generic notification task and source dispatcher.
- `telegram_bot/notifications.py`: add Telegram-specific message formatting and delivery.
- `telegram_bot/process_message.py`: pass Telegram source metadata and enqueue processing without `chat_id`.
- `receipt/views.py`: pass manual-upload source metadata and enqueue processing without `chat_id`.
- `extract_info/tasks.py`: remove Telegram delivery and schedule generic notification task after processing or final failure.
- `extract_info/tests.py` and `receipt/tests.py`: protect the new boundaries and source-specific behavior.

## Tasks

### Task 1: Persist Receipt Source Metadata

- Add failing tests asserting manual and Telegram upload requests carry `source_type` and `source_metadata`.
- Add `source_type` and `source_metadata` to receipt DTOs and the model.
- Add migration `0010_receipt_source_metadata.py`.
- Update create/retry services so source metadata is saved on receipts.

### Task 2: Separate Notification Delivery

- Add failing tests for Telegram completed, needs-review, failed, and manual no-op notifications.
- Create `telegram_bot.notifications` for message formatting and sending.
- Create `receipt.tasks.notify_receipt_processed_task` and dispatcher.

### Task 3: Decouple `process_file_task`

- Add failing tests that `process_file_task` schedules notification and does not call Telegram.
- Remove `chat_id`, `Bot`, token lookup, and message formatting from `extract_info.tasks`.
- Schedule notification after successful processing and after final failure.

### Task 4: Update Entry Points

- Update Telegram upload and voice handlers to save Telegram source metadata and call `process_file_task.delay()` without `chat_id`.
- Update manual upload view to save manual source metadata and call `process_file_task.delay()` without `chat_id`.
- Update tests expecting the old task signature.

### Task 5: Verify

- Run focused tests: `python manage.py test extract_info receipt`.
- Run full tests: `python manage.py test`.
- If database services are unavailable, report the exact command failure.
