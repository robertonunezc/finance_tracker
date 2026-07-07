from datetime import datetime
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from receipt.models import Category, Receipt, ReceiptItem
from reports.services import CategorySpendingService


@override_settings(TIME_ZONE="America/Mexico_City")
class CategorySpendingServiceTests(TestCase):
    def setUp(self):
        self.receipt = Receipt.objects.create(
            user_id="telegram-user",
            purchase_date=timezone.make_aware(datetime(2026, 6, 19, 12)),
            total_amount=Decimal("999.00"),
            image_url="https://example.com/receipt.jpg",
            status="completed",
        )
        ReceiptItem.objects.bulk_create(
            [
                ReceiptItem(
                    receipt=self.receipt,
                    name="Apples",
                    price=10,
                    quantity=2,
                    category=Category.FRUITS,
                ),
                ReceiptItem(
                    receipt=self.receipt,
                    name="Bananas",
                    price=5,
                    quantity=3,
                    category=Category.FRUITS,
                ),
                ReceiptItem(
                    receipt=self.receipt,
                    name="Milk",
                    price=30,
                    quantity=1,
                    category=Category.DAIRY,
                ),
            ]
        )

    def test_aggregates_price_times_quantity_in_one_query(self):
        with self.assertNumQueries(1):
            report = CategorySpendingService.build_report(
                {
                    "period": "custom",
                    "start_date": "2026-06-19",
                    "end_date": "2026-06-19",
                }
            )

        self.assertEqual(report.grand_total, Decimal("65"))
        self.assertEqual(
            [(row.category, row.total) for row in report.rows],
            [
                (Category.FRUITS, Decimal("35")),
                (Category.DAIRY, Decimal("30")),
            ],
        )

    def test_excludes_non_completed_receipts(self):
        self.receipt.status = "processing"
        self.receipt.save(update_fields=["status"])
        report = CategorySpendingService.build_report(
            {
                "period": "custom",
                "start_date": "2026-06-19",
                "end_date": "2026-06-19",
            }
        )
        self.assertEqual(report.grand_total, Decimal("0.00"))

    def test_rejects_reversed_custom_range(self):
        report = CategorySpendingService.build_report(
            {
                "period": "custom",
                "start_date": "2026-06-20",
                "end_date": "2026-06-19",
            }
        )
        self.assertIsNotNone(report.error)


class CategorySpendingViewTests(TestCase):
    def test_report_page_renders(self):
        response = self.client.get(reverse("reports:category-spending"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Where your money went")
        self.assertTemplateUsed(response, "base.html")

    def test_home_page_links_to_category_report(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("reports:category-spending"))
        self.assertTemplateUsed(response, "base.html")
