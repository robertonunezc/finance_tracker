from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Mapping

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from receipt.models import Category, ReceiptItem


@dataclass(frozen=True)
class CategorySpendingRow:
    category: str
    label: str
    total: Decimal
    percentage: Decimal


@dataclass(frozen=True)
class CategorySpendingReport:
    period: str
    start_date: date
    end_date: date
    rows: list[CategorySpendingRow]
    grand_total: Decimal
    error: str | None = None


class CategorySpendingService:
    """Date handling and optimized category aggregation for spending reports."""

    PERIOD_DAY = "day"
    PERIOD_WEEK = "week"
    PERIOD_MONTH = "month"
    PERIOD_CUSTOM = "custom"
    VALID_PERIODS = {PERIOD_DAY, PERIOD_WEEK, PERIOD_MONTH, PERIOD_CUSTOM}

    @classmethod
    def build_report(cls, params: Mapping[str, str]) -> CategorySpendingReport:
        period = params.get("period", cls.PERIOD_MONTH)
        if period not in cls.VALID_PERIODS:
            period = cls.PERIOD_MONTH

        try:
            start_date, end_date = cls._resolve_dates(period, params)
            error = None
        except ValueError as exc:
            period = cls.PERIOD_CUSTOM
            start_date = end_date = timezone.localdate()
            error = str(exc)

        rows, grand_total = cls._category_totals(start_date, end_date)
        return CategorySpendingReport(
            period=period,
            start_date=start_date,
            end_date=end_date,
            rows=rows,
            grand_total=grand_total,
            error=error,
        )

    @classmethod
    def _resolve_dates(
        cls, period: str, params: Mapping[str, str]
    ) -> tuple[date, date]:
        today = timezone.localdate()
        if period == cls.PERIOD_DAY:
            return today, today
        if period == cls.PERIOD_WEEK:
            start = today - timedelta(days=today.weekday())
            return start, today
        if period == cls.PERIOD_MONTH:
            return today.replace(day=1), today

        start_raw = params.get("start_date", "")
        end_raw = params.get("end_date", "")
        try:
            start = date.fromisoformat(start_raw)
            end = date.fromisoformat(end_raw)
        except (TypeError, ValueError):
            raise ValueError("Enter a valid start and end date.") from None
        if start > end:
            raise ValueError("The start date must be before or equal to the end date.")
        return start, end

    @classmethod
    def _category_totals(
        cls, start_date: date, end_date: date
    ) -> tuple[list[CategorySpendingRow], Decimal]:
        current_tz = timezone.get_current_timezone()
        start_at = timezone.make_aware(
            datetime.combine(start_date, time.min), current_tz
        )
        end_at = timezone.make_aware(
            datetime.combine(end_date + timedelta(days=1), time.min), current_tz
        )
        line_total = ExpressionWrapper(
            F("price") * F("quantity"),
            output_field=DecimalField(max_digits=18, decimal_places=2),
        )

        # One query performs filtering, multiplication, grouping, and ordering.
        totals = list(
            ReceiptItem.objects.filter(
                receipt__purchase_date__gte=start_at,
                receipt__purchase_date__lt=end_at,
                receipt__status="completed",
            )
            .values("category")
            .annotate(
                total=Coalesce(
                    Sum(line_total),
                    Decimal("0.00"),
                    output_field=DecimalField(max_digits=18, decimal_places=2),
                )
            )
            .order_by("-total", "category")
        )

        grand_total = sum(
            (Decimal(str(item["total"])) for item in totals), Decimal("0.00")
        )
        labels = dict(Category.choices)
        rows = [
            CategorySpendingRow(
                category=item["category"],
                label=labels.get(
                    item["category"], item["category"].replace("_", " ").title()
                ),
                total=Decimal(str(item["total"])),
                percentage=(
                    Decimal(str(item["total"])) / grand_total * Decimal("100")
                    if grand_total
                    else Decimal("0")
                ),
            )
            for item in totals
        ]
        return rows, grand_total
