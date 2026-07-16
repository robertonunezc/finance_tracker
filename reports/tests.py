from datetime import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
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
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="report-user",
            password="correct-horse-battery-staple",
        )

    def test_anonymous_home_redirects_to_login(self):
        response = self.client.get(reverse("home"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('home')}",
            fetch_redirect_response=False,
        )

    def test_anonymous_report_redirects_to_login(self):
        response = self.client.get(reverse("reports:category-spending"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('reports:category-spending')}",
            fetch_redirect_response=False,
        )

    def test_authenticated_report_page_renders(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("reports:category-spending"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Where your money went")
        self.assertTemplateUsed(response, "base.html")

    def test_authenticated_home_page_links_to_category_report(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("reports:category-spending"))
        self.assertTemplateUsed(response, "base.html")

    def test_login_redirects_to_next_for_valid_credentials(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "report-user",
                "password": "correct-horse-battery-staple",
                "next": reverse("reports:category-spending"),
            },
        )
        self.assertRedirects(
            response,
            reverse("reports:category-spending"),
            fetch_redirect_response=False,
        )

    def test_login_rerenders_errors_for_invalid_credentials(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": "report-user",
                "password": "wrong-password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter a correct username and password")

    def test_logout_redirects_to_login(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
