import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from receipt import extraction_review
from receipt.models import Category, Receipt, ReceiptExtractionReview


@staff_member_required
def review_queue(request):
    reviews = (
        ReceiptExtractionReview.objects.select_related("receipt")
        .prefetch_related("receipt__items")
        .filter(status="needs_review", receipt__status="needs_review")
        .annotate(item_count=Count("receipt__items"))
        .order_by("-updated_at")
    )
    rows = [
        {
            "review": review,
            "receipt": review.receipt,
            "first_item": review.receipt.items.first(),
            "issue_count": len(review.issues or []),
        }
        for review in reviews
    ]
    return render(
        request,
        "receipt/review_queue.html",
        {
            "rows": rows,
        },
    )


@staff_member_required
def review_detail(request, receipt_id):
    receipt = get_object_or_404(
        Receipt.objects.prefetch_related("items"),
        receipt_id=receipt_id,
    )
    review = get_object_or_404(ReceiptExtractionReview, receipt=receipt)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "approve":
            result = extraction_review.approve_review(
                str(receipt.receipt_id),
                request.POST,
                request.user,
            )
            if result.approved:
                messages.success(request, "Receipt approved.")
                return redirect(reverse("receipt-review:queue"))
            messages.error(request, "Approval blocked. Resolve the remaining issues.")
        else:
            extraction_review.save_review_corrections(str(receipt.receipt_id), request.POST)
            messages.success(request, "Corrections saved.")
        return redirect(reverse("receipt-review:detail", args=[receipt.receipt_id]))

    issue_map = _group_issues_by_path(review.issues or [])
    display_payload = _review_display_payload(receipt, review)
    item_rows = _build_item_rows(receipt, display_payload, issue_map)
    return render(
        request,
        "receipt/review_detail.html",
        {
            "receipt": receipt,
            "source_image_url": _source_image_url(receipt),
            "receipt_field_values": _receipt_field_values(display_payload),
            "review": review,
            "item_rows": item_rows,
            "category_options": Category.choices,
            "receipt_field_issues": {
                "store_name": issue_map.get("store_name", []),
                "total": issue_map.get("total", []),
                "subtotal": issue_map.get("subtotal", []),
                "discount": issue_map.get("discount", []),
                "items": issue_map.get("items", []),
            },
        },
    )


@staff_member_required
def review_source(request, receipt_id):
    receipt = get_object_or_404(Receipt, receipt_id=receipt_id)
    image_url = receipt.image_url or ""
    if image_url.startswith(("http://", "https://")):
        return redirect(image_url)

    source_path = _local_source_path(image_url)
    if not source_path or not source_path.is_file():
        raise Http404("Receipt source image not found.")

    content_type, _ = mimetypes.guess_type(source_path.name)
    return FileResponse(open(source_path, "rb"), content_type=content_type or "application/octet-stream")


def _group_issues_by_path(issues):
    grouped = {}
    for issue in issues:
        grouped.setdefault(issue.get("path", ""), []).append(issue)
    return grouped


def _review_display_payload(receipt, review):
    if review.corrected_payload:
        return review.corrected_payload
    extraction_result = receipt.extraction_result or {}
    return extraction_result.get("applied_payload") or review.raw_extraction or {}


def _receipt_field_values(payload):
    return {
        "store_name": _field_display(payload.get("store_name")),
        "total": _field_display(payload.get("total")),
        "subtotal": _field_display(payload.get("subtotal")),
        "discount": _field_display(payload.get("discount")),
    }


def _build_item_rows(receipt, payload, issue_map):
    payload_items = payload.get("items") or []
    if payload_items:
        rows = [
            {
                "index": index,
                "item": {
                    "name": _field_display(item.get("name")),
                    "quantity": _field_display(item.get("quantity"), default="1"),
                    "price": _field_display(item.get("price")),
                    "category": _field_display(item.get("category"), default=Category.OTHER),
                },
            }
            for index, item in enumerate(payload_items)
        ]
    else:
        rows = [
            {
                "index": index,
                "item": {
                    "name": item.name,
                    "quantity": str(item.quantity),
                    "price": f"{item.price:.2f}",
                    "category": item.category,
                },
            }
            for index, item in enumerate(receipt.items.all())
        ]

    for row in rows:
        index = row["index"]
        row["issues"] = {
            "name": issue_map.get(f"items[{index}].name", []),
            "quantity": issue_map.get(f"items[{index}].quantity", []),
            "price": issue_map.get(f"items[{index}].price", []),
            "category": issue_map.get(f"items[{index}].category", []),
        }
    return rows


def _field_display(field, *, default=""):
    value = extraction_review.field_value(field)
    if value in (None, ""):
        return default
    return str(value)


def _source_image_url(receipt):
    if not receipt.image_url:
        return ""
    return reverse("receipt-review:source", args=[receipt.receipt_id])


def _local_source_path(image_url):
    parsed = urlparse(image_url)
    if parsed.scheme or parsed.netloc:
        return None

    relative_path = unquote(parsed.path).lstrip("/")
    media_prefix = str(settings.MEDIA_URL).strip("/")
    if media_prefix and relative_path.startswith(f"{media_prefix}/"):
        relative_path = relative_path[len(media_prefix) + 1:]

    media_root = Path(settings.MEDIA_ROOT).resolve()
    candidate = (media_root / relative_path).resolve()
    try:
        candidate.relative_to(media_root)
    except ValueError:
        return None
    return candidate
