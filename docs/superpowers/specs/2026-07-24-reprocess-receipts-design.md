# Reprocess Receipts Design

## Goal

Add a staff-facing receipt management page where active receipts can be reviewed at a glance, reprocessed from the original source file, or deactivated.

## Scope

- Add a staff-only receipt list page in the existing `receipt` app.
- Show active receipts only.
- Display basic receipt information: receipt id, store, status, purchase date, total amount, item count, source type, and source-file availability.
- Add per-row actions for reprocess and delete.
- Implement delete as a soft deactivate.
- Hide inactive receipts from reports and the receipt management list.
- Reprocess a receipt by clearing extracted data and items while preserving the source file reference.
- Ask staff to confirm reprocess and delete actions before submitting.
- Reuse the existing `process_file_task` extraction pipeline.

## Non Goals

- Build restore/reactivate UI for inactive receipts.
- Hard-delete receipt rows or source files.
- Change OCR prompts, categorization, extraction validation, or manual review behavior beyond clearing old state before reprocessing.
- Add bulk actions.
- Add public receipt management for non-staff users.
- Add live progress polling for queued reprocessing.

## Chosen Approach

Add a focused staff receipt manager beside the existing upload and review pages in the `receipt` app.

The receipt manager will use server-rendered Django views and Bootstrap components, matching the current upload and review implementation. Receipt state transitions will live in `receipt.services` so tests can cover the destructive parts without relying on template behavior.

The delete action will add and use a new `Receipt.is_active` Boolean field. Soft-deleted receipts remain in the database for audit/history, but normal operational queries exclude them.

## Alternatives Considered

### Django Admin Actions Only

Admin actions would be faster to build, but they would not provide the receipt list and confirmation workflow requested for the app UI.

### Hard Delete

Hard delete would remove receipt rows and cascade-delete items, but it risks losing source metadata, duplicate history, and extraction audit data. It also makes accidental deletion harder to recover from.

### Reprocess By Creating A New Receipt

Creating a new receipt for each reprocess would preserve the old extraction state, but it would fragment history and make reports depend on choosing the latest attempt. Resetting the same receipt keeps the existing receipt identity stable.

## Data Model

Add `is_active` to `Receipt`:

```python
is_active = models.BooleanField(default=True, db_index=True)
```

Update the existing per-user file hash uniqueness constraint so it applies only to active receipts with a non-null hash:

```python
models.UniqueConstraint(
    fields=["user_id", "file_hash"],
    condition=models.Q(file_hash__isnull=False, is_active=True),
    name="unique_active_receipt_file_hash_per_user",
)
```

This avoids a soft-deleted receipt blocking a future upload of the same file for the same user.

Update Django admin list display and filters to include `is_active`.

## Receipt Listing UI

Add a staff-only route:

- `GET /receipts/all/`

Use the existing receipt namespace:

- view name: `receipt-review:list`
- template: `receipt/templates/receipt/list.html`

The list should query active receipts only:

```python
Receipt.objects.filter(is_active=True)
```

The query should prefetch or annotate the item count and order by newest updated or created receipts first.

Each row should show:

- receipt id
- store name, defaulting to `Unknown store`
- status
- purchase date
- total amount
- item count
- source type
- a source link when `image_url` is present
- an actions dropdown

Add a staff navigation item labeled `Receipts` beside `Upload` and `Review`.

## Actions

Add two staff-only POST routes:

- `POST /receipts/<receipt_id>/reprocess/`
- `POST /receipts/<receipt_id>/delete/`

Both routes should require CSRF and staff access.

### Reprocess

The row action opens a confirmation modal. The modal copy should make clear that extracted fields and items will be cleared and extraction will run again from the same source file.

On confirmation:

1. Load the active receipt.
2. Clear old extracted data and review state.
3. Delete all receipt items.
4. Reset receipt status to `pending`.
5. Enqueue `process_file_task`.
6. Redirect back to the receipt list with a success message.

The service should preserve:

- `receipt_id`
- `user_id`
- `file_hash`
- `image_url`
- `source_type`
- `source_metadata`
- `is_active=True`
- creation timestamp

The service should reset:

- `status='pending'`
- `purchase_date=timezone.now()`
- `total_amount=Decimal("0.00")`
- `subtotal_amount=None`
- `discount_amount=None`
- `store_name=None`
- `extracted_text=None`
- `extraction_result=None`
- all related `ReceiptItem` rows
- any related `ReceiptExtractionReview` row

The view should infer `file_type` from the saved source path:

- `.pdf` maps to `pdf`
- everything else maps to `image`

If the receipt has no source path, reprocess should not enqueue a task. It should show an error message and leave the receipt unchanged.

### Delete

The row action opens a confirmation modal. The modal copy should make clear that the receipt will be hidden from reports and receipt management.

On confirmation:

1. Load the active receipt.
2. Set `is_active=False`.
3. Redirect back to the receipt list with a success message.

Soft delete should not delete receipt items, review records, source files, or extraction audit data.

## Service API

Add focused service functions in `receipt.services`:

```python
def reset_receipt_for_reprocessing(receipt_id: str) -> Receipt:
    ...

def deactivate_receipt(receipt_id: str) -> Receipt:
    ...

def infer_receipt_file_type(image_url: str) -> str:
    ...
```

`reset_receipt_for_reprocessing()` should be transactional and lock the receipt row with `select_for_update()` before deleting related state.

`deactivate_receipt()` should be transactional and only affect active receipts.

`infer_receipt_file_type()` should keep the view simple and provide deterministic test coverage for PDF routing.

## Reports And Existing Queries

Update report queries so inactive receipts do not contribute to spending totals or item rows:

- `CategorySpendingService._category_totals()`
- `ReceiptItemsService._store_options()`
- `ReceiptItemsService._item_rows()`
- `reports.views.receipt_ticket_image()`

Existing review queue behavior can continue to filter by status, but inactive receipts should not appear there either.

Duplicate upload lookup should search active receipts only. With the active-only unique constraint, a future upload of a previously deactivated receipt can create a fresh active receipt.

## Error Handling

- Non-staff users should be redirected to the admin login, matching existing receipt views.
- Reprocess on an inactive or missing receipt should return 404.
- Delete on an inactive or missing receipt should return 404.
- Reprocess without `image_url` should show an error and not clear data.
- Celery enqueue errors should show an error after the reset if the reset already succeeded; the receipt remains pending so it can be retried.
- Inactive receipts remain hidden from reports even if their status is `completed`.

## Testing

Add service tests:

- active file-hash duplicates still reuse an existing receipt
- inactive file-hash duplicates do not block creating a new active receipt
- reprocess reset deletes receipt items
- reprocess reset deletes extraction review
- reprocess reset clears extracted receipt fields
- reprocess reset preserves source fields
- deactivate sets `is_active=False`
- file type inference returns `pdf` for PDF paths and `image` otherwise

Add view tests:

- receipt list requires staff access
- receipt list shows active receipts and hides inactive receipts
- receipt list shows store, total, item count, status, and action controls
- reprocess action requires staff access
- reprocess action resets the receipt and enqueues `process_file_task`
- reprocess action refuses receipts without a source path
- delete action requires staff access
- delete action hides a receipt from the list

Update report tests:

- inactive completed receipts are excluded from category spending
- inactive completed receipts are excluded from receipt item reports
- inactive receipts cannot serve source images through the report ticket-image endpoint

## Verification

Verification should include:

- focused receipt service tests
- focused receipt manager view tests
- focused report exclusion tests
- existing receipt review tests
- full Django test suite when the local database is available

Manual verification should include logging in as staff, opening the receipt list, confirming row data, confirming delete hides a receipt, and confirming reprocess queues the existing receipt with cleared old data.
