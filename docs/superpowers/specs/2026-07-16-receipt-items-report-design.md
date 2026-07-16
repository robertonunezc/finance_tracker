# Receipt Items Report Design

## Goal

Add a server-rendered report page that lists individual receipt items and lets the user filter them by category, receipt store, and purchase date range.

## Scope

- Add a new page at `/reports/items/` inside the existing `reports` app.
- Show only `ReceiptItem` rows whose parent `Receipt.status` is `completed`.
- Support filters for category, exact store name, start date, and end date.
- Add navigation from the Reports dropdown and the home page.
- Do not add report-specific tests for this feature.

## Architecture

The page follows the existing category report pattern:

- `reports.views.receipt_items` renders the page.
- `ReceiptItemsService` in `reports/services.py` parses query parameters, builds filter options, applies ORM filtering, and returns a report dataclass.
- `reports/templates/reports/receipt_items.html` renders the filter form, summary metrics, and item table.
- `reports/urls.py` exposes the route as `reports:receipt-items`.

This keeps reporting UI and report query logic together in the `reports` app.

## Filters

The service accepts these query parameters:

- `category`: optional `Category` value. Blank means all categories.
- `store_name`: optional exact `Receipt.store_name` value. Blank means all stores.
- `start_date`: optional ISO date.
- `end_date`: optional ISO date.

If no custom date range is provided, the default range is the current month through today, matching the existing category report behavior.

Invalid dates or reversed ranges produce a user-facing error and fall back to a current-day empty-safe range. The template shows the error near the filter form.

## Store Options

The store dropdown is populated from distinct non-empty `Receipt.store_name` values on completed receipts, ordered alphabetically. This prevents free-form store searches and keeps filtering predictable.

## Results

Rows are ordered by newest receipt purchase date first, then item name. Each row includes:

- item name
- category label
- store name, or `Unknown store` when blank
- purchase date
- quantity
- unit price
- line total as `price * quantity`

The report also shows:

- total matching item count
- total amount across matching line totals
- selected reporting period

## UI

The template reuses `reports/reports.css` and Bootstrap styles already used by the category report. The page uses a compact report header, a filter card, summary metrics, and a responsive table. Empty results show a short empty state with guidance to change filters.

## Verification

No report-specific tests will be added, per request. Verification should include running the existing Django test suite if the local database configuration permits it, plus manual smoke validation of the new route/template.
