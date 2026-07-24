import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render

from reports.services import CategorySpendingService, ReceiptItemsService
from receipt.models import Receipt


def category_spending(request):
    """Render category spending using the report service for all business logic."""
    report = CategorySpendingService.build_report(request.GET)
    return render(
        request,
        "reports/category_spending.html",
        {
            "report": report,
            "chart_labels": [row.label for row in report.rows],
            "chart_values": [float(row.total) for row in report.rows],
        },
    )


@login_required(login_url="/admin/login/")
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


@login_required(login_url="/admin/login/")
def receipt_ticket_image(request, receipt_id):
    receipt = get_object_or_404(
        Receipt,
        receipt_id=receipt_id,
        status="completed",
        is_active=True,
    )
    return _ticket_image_response(receipt)


def _ticket_image_response(receipt):
    image_url = receipt.image_url or ""
    if image_url.startswith(("http://", "https://")):
        return _remote_ticket_image_response(image_url)

    source_path = _local_ticket_image_path(image_url)
    if not source_path or not source_path.is_file():
        raise Http404("Receipt ticket image not found.")

    content_type, _ = mimetypes.guess_type(source_path.name)
    return FileResponse(
        open(source_path, "rb"),
        content_type=content_type or "application/octet-stream",
    )


def _remote_ticket_image_response(image_url):
    try:
        remote_response = requests.get(image_url, stream=True, timeout=15)
        remote_response.raise_for_status()
    except requests.RequestException as exc:
        raise Http404("Receipt ticket image not found.") from exc

    content_type = remote_response.headers.get("Content-Type")
    if not content_type:
        content_type, _ = mimetypes.guess_type(urlparse(image_url).path)

    response = StreamingHttpResponse(
        _remote_content_chunks(remote_response),
        content_type=content_type or "application/octet-stream",
    )
    content_length = remote_response.headers.get("Content-Length")
    if content_length:
        response["Content-Length"] = content_length
    return response


def _remote_content_chunks(remote_response):
    try:
        for chunk in remote_response.iter_content(chunk_size=8192):
            if chunk:
                yield chunk
    finally:
        remote_response.close()


def _local_ticket_image_path(image_url):
    parsed = urlparse(image_url)
    if parsed.scheme or parsed.netloc:
        return None

    stored_path = unquote(parsed.path).lstrip("/")
    if not stored_path:
        return None

    media_relative_path = stored_path
    media_prefix = str(settings.MEDIA_URL).strip("/")
    if media_prefix and media_relative_path.startswith(f"{media_prefix}/"):
        media_relative_path = media_relative_path[len(media_prefix) + 1:]

    media_root = Path(settings.MEDIA_ROOT).resolve()
    base_dir = Path(settings.BASE_DIR).resolve()
    candidates = [
        _safe_local_ticket_path(media_root, media_relative_path),
        _safe_local_ticket_path(base_dir, stored_path),
        _safe_local_ticket_path(base_dir, media_relative_path),
    ]
    safe_candidates = [candidate for candidate in candidates if candidate is not None]
    for candidate in safe_candidates:
        if candidate.is_file():
            return candidate
    return safe_candidates[0] if safe_candidates else None


def _safe_local_ticket_path(base_path, relative_path):
    candidate = (base_path / relative_path).resolve()
    try:
        candidate.relative_to(base_path)
    except ValueError:
        return None
    return candidate
