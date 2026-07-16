# Receipt Items Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a server-rendered report page that lists completed receipt items with category, store, and date range filters.

**Architecture:** Add a `ReceiptItemsService` to the existing `reports` service layer, then expose it through a new `reports.views.receipt_items` view and `/reports/items/` route. Render the result with a Bootstrap template that reuses the current report styling and add links from the Reports dropdown and home page.

**Tech Stack:** Django 6.0, Django ORM, Bootstrap 5, existing `reports` app, existing `receipt.models.Category`, `Receipt`, and `ReceiptItem` models.

## Global Constraints

- Page route is `/reports/items/`.
- Only include `ReceiptItem` rows whose parent `Receipt.status` is `completed`.
- Category filter is a dropdown backed by `Category.choices`.
- Store filter is an exact-match dropdown from distinct non-empty completed receipt store names.
- Date range uses `start_date` and `end_date` ISO query parameters.
- Default date range is the current month through today.
- Do not add report-specific tests for this feature.

---

## File Structure

- Modify `reports/services.py`: add receipt item report dataclasses and `ReceiptItemsService`.
- Modify `reports/views.py`: add the `receipt_items` view.
- Modify `reports/urls.py`: add the `reports:receipt-items` URL.
- Create `reports/templates/reports/receipt_items.html`: render filters, summary, and table.
- Modify `templates/base.html`: add the new page to the Reports dropdown.
- Modify `templates/home.html`: add a feature card linking to the new page.
- Modify `reports/static/reports/reports.css`: add compact item report/table helpers if needed.

## Task 1: Add Receipt Items Service

**Files:**
- Modify: `reports/services.py`

**Interfaces:**
- Consumes: `receipt.models.Category`, `receipt.models.Receipt`, `receipt.models.ReceiptItem`.
- Produces: `ReceiptItemsService.build_report(params: Mapping[str, str]) -> ReceiptItemsReport`.
- Produces dataclasses: `ReceiptItemReportRow`, `ReceiptItemsReport`.

- [ ] **Step 1: Add imports and dataclasses**

Add imports:

```python
from receipt.models import Category, Receipt, ReceiptItem
```

Replace the existing `from receipt.models import Category, ReceiptItem` import with the line above.

Add these dataclasses below `CategorySpendingReport`:

```python
@dataclass(frozen=True)
class ReceiptItemReportRow:
    name: str
    category: str
    category_label: str
    store_name: str
    purchase_date: datetime
    quantity: int
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class ReceiptItemsReport:
    start_date: date
    end_date: date
    selected_category: str
    selected_store_name: str
    category_options: list[tuple[str, str]]
    store_options: list[str]
    rows: list[ReceiptItemReportRow]
    item_count: int
    total_amount: Decimal
    error: str | None = None
```

- [ ] **Step 2: Add the service class**

Add this class below `CategorySpendingService`:

```python
class ReceiptItemsService:
    """Filtering and row shaping for the receipt items report."""

    @classmethod
    def build_report(cls, params: Mapping[str, str]) -> ReceiptItemsReport:
        category = params.get("category", "")
        store_name = params.get("store_name", "")
        error = None

        if category and category not in Category.values:
            category = ""
            error = "Choose a valid category."

        try:
            start_date, end_date = cls._resolve_dates(params)
        except ValueError as exc:
            start_date = end_date = timezone.localdate()
            error = str(exc)

        store_options = cls._store_options()
        rows = cls._item_rows(start_date, end_date, category, store_name)
        total_amount = sum((row.line_total for row in rows), Decimal("0.00"))

        return ReceiptItemsReport(
            start_date=start_date,
            end_date=end_date,
            selected_category=category,
            selected_store_name=store_name,
            category_options=list(Category.choices),
            store_options=store_options,
            rows=rows,
            item_count=len(rows),
            total_amount=total_amount,
            error=error,
        )

    @classmethod
    def _resolve_dates(cls, params: Mapping[str, str]) -> tuple[date, date]:
        today = timezone.localdate()
        start_raw = params.get("start_date", "")
        end_raw = params.get("end_date", "")

        if not start_raw and not end_raw:
            return today.replace(day=1), today

        try:
            start = date.fromisoformat(start_raw)
            end = date.fromisoformat(end_raw)
        except (TypeError, ValueError):
            raise ValueError("Enter a valid start and end date.") from None

        if start > end:
            raise ValueError("The start date must be before or equal to the end date.")

        return start, end

    @classmethod
    def _store_options(cls) -> list[str]:
        return list(
            Receipt.objects.filter(status="completed")
            .exclude(store_name__isnull=True)
            .exclude(store_name="")
            .order_by("store_name")
            .values_list("store_name", flat=True)
            .distinct()
        )

    @classmethod
    def _item_rows(
        cls,
        start_date: date,
        end_date: date,
        category: str,
        store_name: str,
    ) -> list[ReceiptItemReportRow]:
        current_tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(datetime.combine(start_date, time.min), current_tz)
        end_at = timezone.make_aware(
            datetime.combine(end_date + timedelta(days=1), time.min), current_tz
        )
        line_total = ExpressionWrapper(
            F("price") * F("quantity"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )

        queryset = (
            ReceiptItem.objects.select_related("receipt")
            .filter(
                receipt__purchase_date__gte=start_at,
                receipt__purchase_date__lt=end_at,
                receipt__status="completed",
            )
            .annotate(line_total=line_total)
            .order_by("-receipt__purchase_date", "name")
        )
        if category:
            queryset = queryset.filter(category=category)
        if store_name:
            queryset = queryset.filter(receipt__store_name=store_name)

        labels = dict(Category.choices)
        return [
            ReceiptItemReportRow(
                name=item.name,
                category=item.category,
                category_label=labels.get(
                    item.category, item.category.replace("_", " ").title()
                ),
                store_name=item.receipt.store_name or "Unknown store",
                purchase_date=item.receipt.purchase_date,
                quantity=item.quantity,
                unit_price=Decimal(str(item.price)),
                line_total=Decimal(str(item.line_total)),
            )
            for item in queryset
        ]
```

- [ ] **Step 3: Review service behavior manually**

Confirm the service:

- defaults dates to month-to-date
- validates invalid category values
- filters only completed receipts
- computes line totals as `price * quantity`
- uses exact store name matching

## Task 2: Add Route and View

**Files:**
- Modify: `reports/views.py`
- Modify: `reports/urls.py`

**Interfaces:**
- Consumes: `ReceiptItemsService.build_report(request.GET)`.
- Produces: route name `reports:receipt-items`.

- [ ] **Step 1: Update `reports/views.py` imports and view**

Change the service import to:

```python
from reports.services import CategorySpendingService, ReceiptItemsService
```

Add this view below `category_spending`:

```python
def receipt_items(request):
    """Render receipt item rows using the report service for all business logic."""
    report = ReceiptItemsService.build_report(request.GET)
    return render(
        request,
        "reports/receipt_items.html",
        {
            "report": report,
        },
    )
```

- [ ] **Step 2: Update `reports/urls.py`**

Add this route to `urlpatterns`:

```python
path("items/", views.receipt_items, name="receipt-items"),
```

Keep the existing category routes unchanged.

## Task 3: Add Receipt Items Template

**Files:**
- Create: `reports/templates/reports/receipt_items.html`

**Interfaces:**
- Consumes: `ReceiptItemsReport` from the view as `report`.

- [ ] **Step 1: Create template**

Create `reports/templates/reports/receipt_items.html` with:

```django
{% extends "base.html" %}
{% load humanize static %}

{% block title %}Receipt items{% endblock %}
{% block reports_nav_class %}active{% endblock %}
{% block styles %}<link rel="stylesheet" href="{% static 'reports/reports.css' %}">{% endblock %}

{% block content %}
<div class="d-flex flex-column flex-lg-row align-items-lg-end justify-content-between gap-3 mb-4">
  <div>
    <p class="eyebrow mb-2">Item report</p>
    <h1 class="display-6 fw-bold mb-2">Receipt items</h1>
    <p class="text-secondary mb-0">Individual items from completed receipts.</p>
  </div>
  <div class="date-summary px-3 py-2">
    <span class="text-secondary small d-block">Selected period</span>
    <strong>{{ report.start_date|date:"M j, Y" }} - {{ report.end_date|date:"M j, Y" }}</strong>
  </div>
</div>

<section class="card report-card border-0 mb-4">
  <div class="card-body p-3 p-lg-4">
    <form method="get" class="row g-3 align-items-end" id="itemFilters">
      <div class="col-12 col-md-6 col-xl-3">
        <label for="categoryFilter" class="form-label small fw-semibold">Category</label>
        <select id="categoryFilter" class="form-select" name="category">
          <option value="">All categories</option>
          {% for value, label in report.category_options %}
            <option value="{{ value }}" {% if report.selected_category == value %}selected{% endif %}>{{ label }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="col-12 col-md-6 col-xl-3">
        <label for="storeFilter" class="form-label small fw-semibold">Store</label>
        <select id="storeFilter" class="form-select" name="store_name">
          <option value="">All stores</option>
          {% for store in report.store_options %}
            <option value="{{ store }}" {% if report.selected_store_name == store %}selected{% endif %}>{{ store }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="col-12 col-sm-6 col-xl-2">
        <label for="startDate" class="form-label small fw-semibold">Start date</label>
        <input id="startDate" class="form-control" type="date" name="start_date" value="{{ report.start_date|date:'Y-m-d' }}">
      </div>
      <div class="col-12 col-sm-6 col-xl-2">
        <label for="endDate" class="form-label small fw-semibold">End date</label>
        <input id="endDate" class="form-control" type="date" name="end_date" value="{{ report.end_date|date:'Y-m-d' }}">
      </div>
      <div class="col-12 col-xl-2">
        <button class="btn btn-dark w-100 px-4">Apply</button>
      </div>
    </form>
    {% if report.error %}<div class="alert alert-danger mt-3 mb-0" role="alert">{{ report.error }}</div>{% endif %}
  </div>
</section>

<section class="row g-4 mb-4" aria-label="Receipt item summary">
  <div class="col-12 col-md-6">
    <div class="card report-card border-0 h-100">
      <div class="card-body p-3 p-lg-4">
        <p class="text-secondary small mb-1">Matching items</p>
        <h2 class="h3 mb-0">{{ report.item_count|intcomma }}</h2>
      </div>
    </div>
  </div>
  <div class="col-12 col-md-6">
    <div class="card report-card border-0 h-100">
      <div class="card-body p-3 p-lg-4">
        <p class="text-secondary small mb-1">Total amount</p>
        <h2 class="h3 mb-0">${{ report.total_amount|floatformat:2|intcomma }}</h2>
      </div>
    </div>
  </div>
</section>

<section class="card report-card border-0">
  <div class="card-body p-0">
    <div class="p-3 p-lg-4 pb-2">
      <h2 class="h5 mb-1">Items</h2>
      <p class="text-secondary small mb-0">Newest receipts first</p>
    </div>
    {% if report.rows %}
      <div class="table-responsive">
        <table class="table report-table item-report-table align-middle mb-0">
          <thead>
            <tr>
              <th>Item</th>
              <th>Category</th>
              <th>Store</th>
              <th>Date</th>
              <th class="text-end">Qty</th>
              <th class="text-end">Unit price</th>
              <th class="text-end">Line total</th>
            </tr>
          </thead>
          <tbody>
            {% for row in report.rows %}
              <tr>
                <td class="fw-semibold">{{ row.name }}</td>
                <td>{{ row.category_label }}</td>
                <td>{{ row.store_name }}</td>
                <td>{{ row.purchase_date|date:"M j, Y" }}</td>
                <td class="text-end">{{ row.quantity }}</td>
                <td class="text-end">${{ row.unit_price|floatformat:2|intcomma }}</td>
                <td class="text-end fw-semibold">${{ row.line_total|floatformat:2|intcomma }}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% else %}
      <div class="empty-state">
        <div class="empty-icon">$</div>
        <h3 class="h5">No items found</h3>
        <p class="text-secondary mb-0">Try another category, store, or date range.</p>
      </div>
    {% endif %}
  </div>
</section>
{% endblock %}
```

## Task 4: Add Navigation Links

**Files:**
- Modify: `templates/base.html`
- Modify: `templates/home.html`

**Interfaces:**
- Consumes: route name `reports:receipt-items`.

- [ ] **Step 1: Add Reports dropdown item**

In `templates/base.html`, add this line below the existing Spending by category dropdown item:

```django
<li><a class="dropdown-item" href="{% url 'reports:receipt-items' %}">Receipt items</a></li>
```

- [ ] **Step 2: Add home feature card**

In `templates/home.html`, add this card below the existing category report card:

```django
<div class="col-12 col-md-6 col-xl-4">
  <div class="card app-card border-0 h-100">
    <div class="card-body p-4">
      <span class="feature-icon mb-3">#</span>
      <h2 class="h5">Receipt items</h2>
      <p class="text-secondary">Review individual receipt lines by category, store, and purchase date range.</p>
      <a href="{% url 'reports:receipt-items' %}" class="stretched-link text-decoration-none">Open report</a>
    </div>
  </div>
</div>
```

## Task 5: Tune Report Styles

**Files:**
- Modify: `reports/static/reports/reports.css`

**Interfaces:**
- Consumes: `.item-report-table` class used by `receipt_items.html`.

- [ ] **Step 1: Add item table helpers**

Append:

```css
.item-report-table td:first-child { min-width: 14rem; }
.item-report-table td:nth-child(3) { min-width: 10rem; }
.item-report-table th, .item-report-table td { white-space: nowrap; }
```

## Task 6: Verification

**Files:**
- No new files.

**Interfaces:**
- Verifies: route rendering, template syntax, existing test suite if database settings permit.

- [ ] **Step 1: Run Django checks**

Run:

```bash
python3 manage.py check
```

Expected:

```text
System check identified no issues (0 silenced).
```

- [ ] **Step 2: Run existing tests only**

Run:

```bash
python3 manage.py test
```

Expected: existing tests pass, or document any local database/environment failure. Do not add report-specific tests.

- [ ] **Step 3: Smoke-render new page with Django test client**

Run:

```bash
python3 manage.py shell -c "from django.test import Client; from django.urls import reverse; c=Client(); r=c.get(reverse('reports:receipt-items')); print(r.status_code); print('Receipt items' in r.content.decode())"
```

Expected:

```text
200
True
```
