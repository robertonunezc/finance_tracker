from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
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
    item_rows = [
        {
            "index": index,
            "item": item,
            "issues": {
                "name": issue_map.get(f"items[{index}].name", []),
                "quantity": issue_map.get(f"items[{index}].quantity", []),
                "price": issue_map.get(f"items[{index}].price", []),
                "category": issue_map.get(f"items[{index}].category", []),
            },
        }
        for index, item in enumerate(receipt.items.all())
    ]
    return render(
        request,
        "receipt/review_detail.html",
        {
            "receipt": receipt,
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


def _group_issues_by_path(issues):
    grouped = {}
    for issue in issues:
        grouped.setdefault(issue.get("path", ""), []).append(issue)
    return grouped
