from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from reports.services import CategorySpendingService


@login_required
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
