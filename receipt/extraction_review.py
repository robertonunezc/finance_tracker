import logging
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone

from receipt.dataclasses import ReceiptItem as ReceiptItemData
from receipt.models import Category, Receipt, ReceiptExtractionReview, ReceiptItem


CONFIDENCE_THRESHOLD = 0.80
ITEM_TOTAL_TOLERANCE = Decimal("1.00")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    overall_confidence: float
    issues: list[dict[str, Any]]

    @property
    def requires_review(self) -> bool:
        return any(issue.get("severity") == "blocking" for issue in self.issues)


@dataclass(frozen=True)
class ExtractionApplicationResult:
    status: str
    total_amount: Decimal
    item_count: int
    validation: ValidationResult


@dataclass(frozen=True)
class ReviewActionResult:
    status: str
    approved: bool
    validation: ValidationResult


def parse_amounts_from_source_text(source_text: str) -> list[Decimal]:
    if not source_text:
        return []

    amounts = []
    amount_pattern = r"(?<![\w.])-?\$?\s*(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?![\w.])"
    for raw_amount in re.findall(amount_pattern, source_text):
        normalized = raw_amount.replace("$", "").replace(",", "").replace(" ", "")
        try:
            amounts.append(Decimal(normalized).quantize(Decimal("0.01")))
        except InvalidOperation:
            continue
    return amounts


def validate_receipt_extraction(payload: Mapping[str, Any]) -> ValidationResult:
    issues: list[dict[str, Any]] = []
    confidences: list[float] = []

    _validate_required_receipt_fields(payload, issues)
    _collect_confidence_issues(payload, issues, confidences)
    _validate_source_amount("total", payload.get("total"), issues)

    for index, item in enumerate(payload.get("items") or []):
        _validate_required_item_fields(index, item, issues)
        _validate_source_amount(f"items[{index}].price", item.get("price"), issues)

    _validate_item_sum(payload, issues)

    overall_confidence = min(confidences) if confidences else 0.0
    if issues:
        overall_confidence = min(overall_confidence, CONFIDENCE_THRESHOLD - 0.01)
    return ValidationResult(
        overall_confidence=max(0.0, round(float(overall_confidence), 4)),
        issues=issues,
    )


def build_extraction_payload(ticket: Any, items: list[ReceiptItemData] | None = None) -> dict[str, Any]:
    if isinstance(ticket, Mapping):
        payload = _json_safe(ticket)
    elif hasattr(ticket, "model_dump"):
        payload = ticket.model_dump(mode="json")
    else:
        payload = {
            "store_name": _coerce_field(getattr(ticket, "store_name", None)),
            "subtotal": _coerce_field(getattr(ticket, "subtotal", None)),
            "discount": _coerce_field(getattr(ticket, "discount", None)),
            "total": _coerce_field(getattr(ticket, "total", None)),
            "items": [
                {
                    "name": _coerce_field(getattr(item, "name", None)),
                    "price": _coerce_field(getattr(item, "price", None)),
                    "quantity": _coerce_field(getattr(item, "quantity", 1)),
                    "category": _coerce_field(getattr(item, "category", Category.OTHER)),
                }
                for item in getattr(ticket, "items", []) or []
            ],
        }

    if items:
        payload_items = payload.setdefault("items", [])
        for index, item in enumerate(items):
            if index >= len(payload_items):
                payload_items.append({})
            payload_items[index].setdefault("name", _coerce_field(item.name))
            payload_items[index].setdefault("price", _coerce_field(item.price))
            payload_items[index].setdefault("quantity", _coerce_field(item.quantity or 1))
            payload_items[index]["category"] = _merge_field_value(
                payload_items[index].get("category"),
                item.category or Category.OTHER,
            )

    return payload


def apply_extraction_result(
    receipt_id: str,
    ticket: Any,
    items: list[ReceiptItemData] | None,
) -> ExtractionApplicationResult:
    raw_payload = build_extraction_payload(ticket)
    payload = build_extraction_payload(raw_payload, items)
    try:
        validation = validate_receipt_extraction(payload)
    except Exception as exc:
        logger.error("Receipt extraction validation failed for %s: %s", receipt_id, exc)
        validation = ValidationResult(
            overall_confidence=0.0,
            issues=[
                _issue(
                    path="extraction",
                    code="validation_error",
                    message="Validation failed unexpectedly and requires manual review.",
                    extracted_value=exc,
                    source_text="",
                )
            ],
        )
    status = "needs_review" if validation.requires_review else "completed"
    total_amount = _field_decimal(payload.get("total")) or Decimal("0.00")

    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id)
        _save_receipt_values(receipt, raw_payload, payload, status, validation)
        _replace_receipt_items(receipt, payload, items)

        if validation.requires_review:
            ReceiptExtractionReview.objects.update_or_create(
                receipt=receipt,
                defaults={
                    "status": "needs_review",
                    "overall_confidence": validation.overall_confidence,
                    "issues": validation.issues,
                    "raw_extraction": raw_payload,
                    "approved_by": None,
                    "approved_at": None,
                },
            )
        else:
            ReceiptExtractionReview.objects.filter(receipt=receipt).delete()

    return ExtractionApplicationResult(
        status=status,
        total_amount=total_amount,
        item_count=len(items or payload.get("items") or []),
        validation=validation,
    )


def save_review_corrections(receipt_id: str, form_data: Mapping[str, Any]) -> ReviewActionResult:
    return _apply_review_action(receipt_id, form_data, approve=False, user=None)


def approve_review(
    receipt_id: str,
    form_data: Mapping[str, Any],
    user: Any,
) -> ReviewActionResult:
    return _apply_review_action(receipt_id, form_data, approve=True, user=user)


def field_value(field: Any) -> Any:
    return _field_raw_value(field)


def _validate_required_receipt_fields(payload: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    total = payload.get("total")
    if _field_decimal(total) is None:
        issues.append(_issue(
            path="total",
            code="missing_required_value",
            message="Receipt total is required.",
            extracted_value=_field_raw_value(total),
            source_text=_field_source(total),
        ))


def _validate_required_item_fields(index: int, item: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    name = item.get("name")
    price = item.get("price")
    category = item.get("category")

    if not str(_field_raw_value(name) or "").strip():
        issues.append(_issue(
            path=f"items[{index}].name",
            code="missing_required_value",
            message="Item name is required.",
            extracted_value=_field_raw_value(name),
            source_text=_field_source(name),
        ))

    if _field_decimal(price) is None:
        issues.append(_issue(
            path=f"items[{index}].price",
            code="missing_required_value",
            message="Item price is required.",
            extracted_value=_field_raw_value(price),
            source_text=_field_source(price),
        ))

    category_value = str(_field_raw_value(category) or "").strip()
    if category_value not in Category.values:
        issues.append(_issue(
            path=f"items[{index}].category",
            code="missing_required_value",
            message="Item category must be a valid category.",
            extracted_value=category_value,
            source_text=_field_source(category),
        ))


def _collect_confidence_issues(
    payload: Mapping[str, Any],
    issues: list[dict[str, Any]],
    confidences: list[float],
) -> None:
    for field_name in ("store_name", "total", "subtotal", "discount"):
        _collect_field_confidence(field_name, payload.get(field_name), issues, confidences)

    for index, item in enumerate(payload.get("items") or []):
        for field_name in ("name", "price", "quantity", "category"):
            _collect_field_confidence(
                f"items[{index}].{field_name}",
                item.get(field_name),
                issues,
                confidences,
            )


def _collect_field_confidence(
    path: str,
    field: Any,
    issues: list[dict[str, Any]],
    confidences: list[float],
) -> None:
    if not isinstance(field, Mapping):
        return

    confidence = field.get("confidence")
    if confidence is None:
        return

    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0

    confidence_value = min(max(confidence_value, 0.0), 1.0)
    confidences.append(confidence_value)
    if confidence_value < CONFIDENCE_THRESHOLD:
        issues.append(_issue(
            path=path,
            code="low_confidence",
            message="Field confidence is below the review threshold.",
            extracted_value=_field_raw_value(field),
            source_text=_field_source(field),
        ))


def _validate_source_amount(path: str, field: Any, issues: list[dict[str, Any]]) -> None:
    extracted_amount = _field_decimal(field)
    if extracted_amount is None:
        return
    if isinstance(field, Mapping) and field.get("reviewed"):
        return

    source_text = _field_source(field)
    source_amounts = parse_amounts_from_source_text(source_text)
    if not source_amounts:
        return

    if extracted_amount not in source_amounts:
        issues.append(_issue(
            path=path,
            code="source_amount_mismatch",
            message="Extracted amount differs from source evidence.",
            extracted_value=str(extracted_amount),
            source_text=source_text,
        ))


def _validate_item_sum(payload: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    total = _field_decimal(payload.get("total"))
    if total is None:
        return

    line_total = Decimal("0.00")
    saw_item_price = False
    for item in payload.get("items") or []:
        price = _field_decimal(item.get("price"))
        if price is None:
            continue
        saw_item_price = True
        quantity = _field_decimal(item.get("quantity")) or Decimal("1")
        line_total += price * quantity

    if saw_item_price and abs(line_total - total) > ITEM_TOTAL_TOLERANCE:
        issues.append(_issue(
            path="total",
            code="item_sum_mismatch",
            message="Receipt total differs from the sum of item line totals.",
            extracted_value=str(total),
            source_text=_field_source(payload.get("total")),
        ))


def _field_raw_value(field: Any) -> Any:
    if isinstance(field, Mapping):
        return field.get("value")
    if hasattr(field, "value"):
        return getattr(field, "value")
    return field


def _field_source(field: Any) -> str:
    if isinstance(field, Mapping):
        return str(field.get("source_text") or "")
    if hasattr(field, "source_text"):
        return str(getattr(field, "source_text") or "")
    return ""


def _field_decimal(field: Any) -> Decimal | None:
    value = _field_raw_value(field)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def _issue(
    *,
    path: str,
    code: str,
    message: str,
    extracted_value: Any,
    source_text: str,
) -> dict[str, Any]:
    return {
        "path": path,
        "code": code,
        "severity": "blocking",
        "message": message,
        "extracted_value": "" if extracted_value is None else str(extracted_value),
        "source_text": source_text,
    }


def _save_receipt_values(
    receipt: Receipt,
    raw_payload: Mapping[str, Any],
    payload: Mapping[str, Any],
    status: str,
    validation: ValidationResult,
) -> None:
    receipt.purchase_date = timezone.now()
    receipt.total_amount = _field_decimal(payload.get("total")) or Decimal("0.00")
    receipt.subtotal_amount = _field_decimal(payload.get("subtotal"))
    receipt.discount_amount = _field_decimal(payload.get("discount"))
    receipt.store_name = _field_raw_value(payload.get("store_name")) or None
    receipt.status = status
    receipt.extraction_result = {
        "raw_extraction": raw_payload,
        "applied_payload": payload,
        "validation": {
            "overall_confidence": validation.overall_confidence,
            "requires_review": validation.requires_review,
            "issues": validation.issues,
        },
    }
    receipt.save(
        update_fields=[
            "purchase_date",
            "total_amount",
            "subtotal_amount",
            "discount_amount",
            "store_name",
            "status",
            "extraction_result",
            "updated_at",
        ]
    )


def _replace_receipt_items(
    receipt: Receipt,
    payload: Mapping[str, Any],
    items: list[ReceiptItemData] | None,
) -> None:
    receipt.items.all().delete()
    if items is None:
        items = [_payload_item_to_dataclass(item) for item in payload.get("items") or []]

    for item in items:
        ReceiptItem.objects.create(
            receipt=receipt,
            name=item.name,
            price=float(item.price),
            quantity=int(item.quantity or 1),
            category=item.category or Category.OTHER,
            embedding=item.embedding,
        )


def _payload_item_to_dataclass(item: Mapping[str, Any]) -> ReceiptItemData:
    return ReceiptItemData(
        name=str(_field_raw_value(item.get("name")) or ""),
        price=float(_field_decimal(item.get("price")) or Decimal("0.00")),
        quantity=int(_field_decimal(item.get("quantity")) or Decimal("1")),
        category=str(_field_raw_value(item.get("category")) or Category.OTHER),
    )


def _coerce_field(value: Any, confidence: float = 1.0) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _json_safe(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "value"):
        return {
            "value": _json_safe(getattr(value, "value")),
            "source_text": str(getattr(value, "source_text", "") or ""),
            "confidence": _clamp_confidence(getattr(value, "confidence", confidence)),
        }
    return {
        "value": _json_safe(value),
        "source_text": "" if value is None else str(value),
        "confidence": _clamp_confidence(confidence),
    }


def _merge_field_value(field: Any, value: Any) -> dict[str, Any]:
    merged = _coerce_field(field)
    merged["value"] = _json_safe(value)
    return merged


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _json_safe(inner_value) for key, inner_value in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _clamp_confidence(value: Any) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = 0.0
    return min(max(confidence, 0.0), 1.0)


def _apply_review_action(
    receipt_id: str,
    form_data: Mapping[str, Any],
    *,
    approve: bool,
    user: Any,
) -> ReviewActionResult:
    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id)
        review = ReceiptExtractionReview.objects.select_for_update().get(receipt=receipt)
        corrected_payload = _build_corrected_payload(form_data, review.raw_extraction)
        validation = validate_receipt_extraction(corrected_payload)
        approved = approve and not validation.requires_review
        receipt_status = "completed" if approved else "needs_review"

        _save_receipt_values(receipt, review.raw_extraction, corrected_payload, receipt_status, validation)
        _replace_receipt_items(receipt, corrected_payload, items=None)

        review.corrected_payload = corrected_payload
        review.overall_confidence = validation.overall_confidence
        review.issues = validation.issues
        if approved:
            review.status = "approved"
            review.approved_by = _user_label(user)
            review.approved_at = timezone.now()
        else:
            review.status = "needs_review"
            review.approved_by = None
            review.approved_at = None
        review.save(
            update_fields=[
                "corrected_payload",
                "overall_confidence",
                "issues",
                "status",
                "approved_by",
                "approved_at",
                "updated_at",
            ]
        )

    return ReviewActionResult(
        status=receipt_status,
        approved=approved,
        validation=validation,
    )


def _build_corrected_payload(
    form_data: Mapping[str, Any],
    raw_extraction: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "store_name": _corrected_field(
            form_data.get("store_name"),
            raw_extraction.get("store_name"),
        ),
        "subtotal": _corrected_field(
            form_data.get("subtotal_amount"),
            raw_extraction.get("subtotal"),
        ),
        "discount": _corrected_field(
            form_data.get("discount_amount"),
            raw_extraction.get("discount"),
        ),
        "total": _corrected_field(
            form_data.get("total_amount"),
            raw_extraction.get("total"),
        ),
        "items": [
            _corrected_item(form_data, raw_extraction, index)
            for index in range(_form_int(form_data.get("item_count"), default=0))
        ],
    }


def _corrected_item(
    form_data: Mapping[str, Any],
    raw_extraction: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    raw_items = raw_extraction.get("items") or []
    raw_item = raw_items[index] if index < len(raw_items) else {}
    return {
        "name": _corrected_field(form_data.get(f"item_{index}_name"), raw_item.get("name")),
        "price": _corrected_field(form_data.get(f"item_{index}_price"), raw_item.get("price")),
        "quantity": _corrected_field(form_data.get(f"item_{index}_quantity") or "1", raw_item.get("quantity")),
        "category": _corrected_field(form_data.get(f"item_{index}_category"), raw_item.get("category")),
    }


def _corrected_field(value: Any, raw_field: Any) -> dict[str, Any]:
    return {
        "value": "" if value is None else str(value).strip(),
        "source_text": _field_source(raw_field),
        "confidence": 1.0,
        "reviewed": True,
    }


def _form_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _user_label(user: Any) -> str:
    if hasattr(user, "get_username"):
        username = user.get_username()
        if username:
            return username
    if hasattr(user, "username") and user.username:
        return str(user.username)
    return str(user)
