import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone

from extract_info import services as extract_info_service
from receipt.dataclasses import ReceiptItem as ReceiptItemData
from receipt.models import Category, Receipt, ReceiptExtractionReview, ReceiptItem


CONFIDENCE_THRESHOLD = 0.80
ITEM_TOTAL_TOLERANCE = Decimal("1.00")
OPTIONAL_CONFIDENCE_PATHS = {"subtotal", "discount"}
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
    _validate_items_present(payload, issues)
    _collect_confidence_issues(payload, issues, confidences)
    _validate_source_amount("total", payload.get("total"), issues)

    for index, item in enumerate(payload.get("items") or []):
        _validate_required_item_fields(index, item, issues)
        _validate_source_amount(f"items[{index}].line_total", item.get("line_total"), issues)
        _validate_source_amount(f"items[{index}].unit_price", item.get("unit_price"), issues)

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
        payload = _normalize_payload_item_amounts(_json_safe(ticket))
    elif hasattr(ticket, "model_dump"):
        payload = _normalize_payload_item_amounts(ticket.model_dump(mode="json"))
    else:
        payload = {
            "store_name": _coerce_field(getattr(ticket, "store_name", None)),
            "subtotal": _coerce_field(getattr(ticket, "subtotal", None)),
            "discount": _coerce_field(getattr(ticket, "discount", None)),
            "total": _coerce_field(getattr(ticket, "total", None)),
            "items": [
                {
                    "name": _coerce_field(getattr(item, "name", None)),
                    "unit_price": _coerce_field(getattr(item, "unit_price", None)),
                    "line_total": _coerce_field(_object_item_line_total(item)),
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
            payload_items[index].setdefault("unit_price", _coerce_field(item.unit_price))
            payload_items[index].setdefault("line_total", _coerce_field(item.line_total))
            payload_items[index].setdefault("quantity", _coerce_field(item.quantity or 1))
            payload_items[index]["category"] = _merge_field_value(
                payload_items[index].get("category"),
                item.category or Category.OTHER,
            )
            if item.category_confidence is not None:
                payload_items[index]["category"]["confidence"] = _clamp_confidence(item.category_confidence)

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


def _normalize_payload_item_amounts(payload: dict[str, Any]) -> dict[str, Any]:
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        if "line_total" not in item and "price" in item:
            item["line_total"] = item["price"]
        item.pop("price", None)
    return payload


def _object_item_line_total(item: Any) -> Any:
    line_total = getattr(item, "line_total", None)
    if line_total is not None:
        return line_total
    return getattr(item, "price", None)


def _validate_required_receipt_fields(payload: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    total = payload.get("total")
    total_amount = _field_decimal(total)
    if total_amount is None:
        issues.append(_issue(
            path="total",
            code="missing_required_value",
            message="Receipt total is required.",
            extracted_value=_field_raw_value(total),
            source_text=_field_source(total),
        ))
    elif total_amount <= Decimal("0.00"):
        issues.append(_issue(
            path="total",
            code="invalid_amount",
            message="Receipt total must be greater than zero.",
            extracted_value=_field_raw_value(total),
            source_text=_field_source(total),
        ))


def _validate_items_present(payload: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    if payload.get("items"):
        return

    issues.append(_issue(
        path="items",
        code="missing_items",
        message="At least one receipt item is required.",
        extracted_value="",
        source_text="",
    ))


def _validate_required_item_fields(index: int, item: Mapping[str, Any], issues: list[dict[str, Any]]) -> None:
    name = item.get("name")
    line_total = item.get("line_total")
    quantity = item.get("quantity")
    category = item.get("category")

    if not str(_field_raw_value(name) or "").strip():
        issues.append(_issue(
            path=f"items[{index}].name",
            code="missing_required_value",
            message="Item name is required.",
            extracted_value=_field_raw_value(name),
            source_text=_field_source(name),
        ))

    line_total_amount = _field_decimal(line_total)
    if line_total_amount is None:
        issues.append(_issue(
            path=f"items[{index}].line_total",
            code="missing_required_value",
            message="Item line total is required.",
            extracted_value=_field_raw_value(line_total),
            source_text=_field_source(line_total),
        ))
    elif line_total_amount <= Decimal("0.00"):
        issues.append(_issue(
            path=f"items[{index}].line_total",
            code="invalid_amount",
            message="Item line total must be greater than zero.",
            extracted_value=_field_raw_value(line_total),
            source_text=_field_source(line_total),
        ))

    if _field_positive_quantity(quantity) is None:
        issues.append(_issue(
            path=f"items[{index}].quantity",
            code="invalid_quantity",
            message="Item quantity must be a positive number.",
            extracted_value=_field_raw_value(quantity),
            source_text=_field_source(quantity),
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
        for field_name in ("name", "unit_price", "line_total", "quantity", "category"):
            if field_name == "unit_price" and _field_is_blank(item.get(field_name)):
                continue
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
    if path in OPTIONAL_CONFIDENCE_PATHS and _field_is_blank(field):
        return

    if not isinstance(field, Mapping):
        _add_missing_confidence_issue(path, field, issues, confidences)
        return

    confidence = field.get("confidence")
    if confidence is None:
        _add_missing_confidence_issue(path, field, issues, confidences)
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


def _add_missing_confidence_issue(
    path: str,
    field: Any,
    issues: list[dict[str, Any]],
    confidences: list[float],
) -> None:
    confidences.append(0.0)
    issues.append(_issue(
        path=path,
        code="missing_confidence",
        message="Field confidence metadata is missing.",
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
        issues.append(_issue(
            path=path,
            code="missing_source_evidence",
            message="Amount source evidence is missing or does not contain a parseable amount.",
            extracted_value=str(extracted_amount),
            source_text=source_text,
        ))
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
    if total is None or total <= Decimal("0.00"):
        return

    discount = _field_decimal(payload.get("discount")) or Decimal("0.00")
    items_total = Decimal("0.00")
    saw_item_total = False
    for item in payload.get("items") or []:
        line_total = _field_decimal(item.get("line_total"))
        if line_total is None or line_total <= Decimal("0.00"):
            continue
        saw_item_total = True
        items_total += line_total

    adjusted_item_total = items_total - discount
    difference = abs(adjusted_item_total - total)
    if saw_item_total and difference > ITEM_TOTAL_TOLERANCE:
        issue = _issue(
            path="total",
            code="item_sum_mismatch",
            message="Receipt total differs from the sum of item line totals.",
            extracted_value=str(total),
            source_text=_field_source(payload.get("total")),
        )
        issue["details"] = {
            "receipt_total": str(total),
            "item_line_total_sum": str(items_total.quantize(Decimal("0.01"))),
            "discount": str(discount.quantize(Decimal("0.01"))),
            "adjusted_item_total": str(adjusted_item_total.quantize(Decimal("0.01"))),
            "difference": str(difference.quantize(Decimal("0.01"))),
            "tolerance": str(ITEM_TOTAL_TOLERANCE),
        }
        issues.append(issue)


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


def _optional_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _field_positive_quantity(field: Any) -> Decimal | None:
    return _positive_quantity_value(_field_raw_value(field))


def _positive_quantity_value(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        quantity = Decimal(str(value).replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if quantity <= Decimal("0.000"):
        return None
    return quantity.quantize(Decimal("0.001"))


def _field_is_blank(field: Any) -> bool:
    return str(_field_raw_value(field) or "").strip() == ""


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
    *,
    item_embedding_fn: Callable[[str], list[float]] | None = None,
) -> None:
    receipt.items.all().delete()
    if items is None:
        items = [
            _payload_item_to_dataclass(item, item_embedding_fn=item_embedding_fn)
            for item in payload.get("items") or []
        ]

    for item in items:
        ReceiptItem.objects.create(
            receipt=receipt,
            name=item.name,
            unit_price=item.unit_price,
            line_total=item.line_total,
            quantity=_positive_quantity_value(item.quantity) or Decimal("1.000"),
            category=item.category or Category.OTHER,
            embedding=item.embedding,
        )


def _payload_item_to_dataclass(
    item: Mapping[str, Any],
    *,
    item_embedding_fn: Callable[[str], list[float]] | None = None,
) -> ReceiptItemData:
    name = str(_field_raw_value(item.get("name")) or "")
    embedding = _item_embedding(name, item_embedding_fn)
    return ReceiptItemData(
        name=name,
        unit_price=_optional_float(_field_decimal(item.get("unit_price"))),
        line_total=float(_field_decimal(item.get("line_total")) or Decimal("0.00")),
        quantity=_field_positive_quantity(item.get("quantity")) or Decimal("1.000"),
        category=str(_field_raw_value(item.get("category")) or Category.OTHER),
        embedding=embedding,
    )


def _item_embedding(
    name: str,
    item_embedding_fn: Callable[[str], list[float]] | None,
) -> list[float] | None:
    if not item_embedding_fn or not name:
        return None
    try:
        return item_embedding_fn(name)
    except Exception:
        logger.warning("Failed to generate corrected item embedding for %s", name, exc_info=True)
        return None


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
    item_embedding_fn: Callable[[str], list[float]] | None = None,
) -> ReviewActionResult:
    with transaction.atomic():
        receipt = Receipt.objects.select_for_update().get(receipt_id=receipt_id)
        review = ReceiptExtractionReview.objects.select_for_update().get(receipt=receipt)
        corrected_payload = _build_corrected_payload(form_data, review.raw_extraction)
        validation = validate_receipt_extraction(corrected_payload)
        approved = approve and not validation.requires_review
        receipt_status = "completed" if approved else "needs_review"
        if approved and item_embedding_fn is None:
            item_embedding_fn = extract_info_service.generate_embedding
        if not approved:
            item_embedding_fn = None

        _save_receipt_values(receipt, review.raw_extraction, corrected_payload, receipt_status, validation)
        _replace_receipt_items(receipt, corrected_payload, items=None, item_embedding_fn=item_embedding_fn)

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
    items = []
    for index in range(_form_int(form_data.get("item_count"), default=0)):
        if _form_truthy(form_data.get(f"item_{index}_delete")):
            continue
        items.append(_corrected_item(form_data, raw_extraction, index))

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
        "items": items,
    }


def _corrected_item(
    form_data: Mapping[str, Any],
    raw_extraction: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    raw_items = raw_extraction.get("items") or []
    raw_item = raw_items[index] if index < len(raw_items) else {}
    raw_line_total = raw_item.get("line_total") or raw_item.get("price")
    line_total_value = form_data.get(f"item_{index}_line_total")
    if line_total_value is None:
        line_total_value = form_data.get(f"item_{index}_price")
    return {
        "name": _corrected_field(form_data.get(f"item_{index}_name"), raw_item.get("name")),
        "unit_price": _corrected_field(form_data.get(f"item_{index}_unit_price"), raw_item.get("unit_price")),
        "line_total": _corrected_field(line_total_value, raw_line_total),
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


def _form_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _user_label(user: Any) -> str:
    if hasattr(user, "get_username"):
        username = user.get_username()
        if username:
            return username
    if hasattr(user, "username") and user.username:
        return str(user.username)
    return str(user)
