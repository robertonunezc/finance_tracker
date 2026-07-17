import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from receipt.models import Category


CONFIDENCE_THRESHOLD = 0.80
ITEM_TOTAL_TOLERANCE = Decimal("1.00")


@dataclass(frozen=True)
class ValidationResult:
    overall_confidence: float
    issues: list[dict[str, Any]]

    @property
    def requires_review(self) -> bool:
        return any(issue.get("severity") == "blocking" for issue in self.issues)


def parse_amounts_from_source_text(source_text: str) -> list[Decimal]:
    if not source_text:
        return []

    amounts = []
    for raw_amount in re.findall(r"(?<!\w)-?\$?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})?|-?\$?\s*\d+(?:\.\d{2})", source_text):
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
    return field


def _field_source(field: Any) -> str:
    if isinstance(field, Mapping):
        return str(field.get("source_text") or "")
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
