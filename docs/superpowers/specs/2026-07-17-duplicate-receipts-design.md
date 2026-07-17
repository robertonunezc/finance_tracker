# Duplicate Receipts Design

## Goal

Prevent duplicate receipt records when a user uploads the exact same receipt file more than once. A duplicate upload should reuse the existing receipt for that user instead of creating a second expense.

## Scope

- Detect exact duplicate uploaded receipt images by hashing the uploaded file bytes.
- De-duplicate per user, not globally across all users.
- Skip extraction when the matching receipt is already completed.
- Retry the same receipt when the matching receipt is failed.
- Avoid enqueueing a second extraction job when the matching receipt is already pending or processing.
- Keep existing receipt item replacement behavior when a reused receipt is processed again.

This design applies to the Telegram photo receipt upload path. Voice notes and future bank statement processing can adopt the same pattern later, but they are not part of this change.

## Non Goals

- Detect visually similar but byte-different images, such as compressed screenshots, cropped images, or rescanned receipts.
- Merge different receipts that happen to have the same total, date, or store.
- Build a user-facing duplicate management UI.
- Change the extraction prompt or item categorization behavior.

## Chosen Approach

Use a SHA-256 hash of the uploaded file bytes and store it on `Receipt`.

The `Receipt` model will gain a nullable `file_hash` field. New uploads will compute the hash from the temporary downloaded file before extraction. The app will then look for an existing receipt with the same `user_id` and `file_hash`.

A database uniqueness constraint on `(user_id, file_hash)` for non-null hashes will be the final guard against race conditions. Existing receipts can keep `NULL` hashes so the migration is safe for current data.

## Alternatives Considered

### Telegram-only lookup without a database constraint

This would be easy to add in the upload handler, but it could still create duplicates if the same file is uploaded twice at nearly the same time. It also spreads duplicate behavior into bot code instead of centralizing it in the receipt service layer.

### Post-extraction duplicate detection

This could compare store, date, total, and items after extraction. It might catch byte-different copies of the same receipt, but it costs an extraction request before detecting the duplicate and is more likely to produce false positives or false negatives.

## Data Model

Add this field to `Receipt`:

- `file_hash`: nullable 64-character string containing the SHA-256 hex digest of the original uploaded file bytes.

Add a conditional unique constraint:

- unique `(user_id, file_hash)` where `file_hash IS NOT NULL`

This allows:

- one completed receipt per user per exact uploaded file
- different users to upload the same file independently
- old rows without a hash to remain valid

## Service API

Add receipt service helpers so duplicate logic is not embedded directly in the Telegram handler:

- `compute_file_sha256(file_path)`: streams a local file and returns the SHA-256 hex digest.
- `get_receipt_by_user_and_file_hash(user_id, file_hash)`: returns the matching receipt data or `None`.
- `create_receipt_with_file_hash(receipt_data, file_hash)`: creates a new pending receipt with the uploaded image URL and hash, or returns the existing matching receipt if a database race is detected.

The create or lookup result should return enough metadata for callers to decide what to do next:

- the receipt ID
- the current status
- whether the receipt was newly created or reused
- the current image URL, if present

The create operation should run inside a transaction and handle `IntegrityError`. If two uploads race after both have passed the initial lookup, the loser should fetch and reuse the row created by the winner. This may result in an unnecessary file upload in the race case, but it still prevents duplicate database receipts and duplicate expenses.

## Telegram Upload Flow

The photo receipt flow in `telegram_bot/process_message.py` will become:

1. Authenticate the Telegram user.
2. Download the Telegram photo to a temporary local file.
3. Compute `file_hash` from the temporary file.
4. Resolve `user_id` using the existing username, first name, or Telegram ID fallback.
5. Look up an existing receipt with the same `user_id` and `file_hash`.
6. If no matching receipt exists:
   - upload the file through the existing upload service
   - create the receipt with the uploaded image URL and `file_hash`
   - reply with the new receipt ID and pending status
   - enqueue `process_file_task`
7. If the receipt is reused and already `completed`:
   - do not upload the file again
   - do not enqueue extraction
   - reply that the receipt was already uploaded and include the existing receipt ID
8. If the receipt is reused and `processing`:
   - do not enqueue a second task
   - reply that the existing receipt is already processing
9. If the receipt is reused and `pending`:
   - do not enqueue a second task
   - reply that the existing receipt is already queued for processing
10. If the receipt is reused and `failed`:
   - upload the latest file if the receipt has no usable image URL or if the previous local file path is no longer usable
   - set status to `pending`
   - enqueue `process_file_task` for the same receipt ID

## Extraction Task Behavior

`extract_info/tasks.py` can mostly keep its current behavior. The existing `update_receipt(..., items=...)` implementation deletes old items and creates the extracted item set again, which matches the retry behavior for a reused failed receipt.

One bug should be fixed as part of this work: when retries are exhausted, the task should set status to `failed`, not `error`, because `failed` is the status defined in `STATUS_CHOICES`.

## Error Handling

- If upload fails before a brand-new receipt is created, reply with an error and do not create a receipt.
- If a reused failed receipt is retried and upload fails again, keep it `failed`.
- If extraction fails after all Celery retries, mark the reused or new receipt `failed`.
- If a duplicate upload maps to `pending` or `processing`, leave the existing receipt untouched and do not enqueue another task.
- Temporary local files should still be cleaned up when the request path can do so safely.

## Testing

Add focused Django tests around the service layer and task behavior:

- same user plus same hash reuses one receipt
- different users plus same hash creates separate receipts
- completed duplicate is reported as reused and should not require extraction
- pending or processing duplicate is reported as reused and does not enqueue another task
- failed duplicate reuses the same receipt and can be moved back to pending
- task failure writes `failed`, not `error`

Add a small hash test to verify SHA-256 is computed from file bytes consistently.

The Telegram handler can be tested with lightweight mocks if the existing test setup makes that practical. The service tests are the core coverage because they protect the database-level de-duplication contract.

## Verification

Verification should include:

- running the focused receipt tests
- running the extraction task status test
- running the full Django test suite if the local database is available
- manually checking that duplicate upload branches do not enqueue duplicate work

## Rollout Notes

The migration is backward compatible because `file_hash` is nullable. Existing receipts will not be de-duplicated retroactively. Duplicate protection starts for new uploads after the migration is applied.
