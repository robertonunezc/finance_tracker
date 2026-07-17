from django.contrib import admin


# Register your models here.

from .models import Receipt, ReceiptExtractionReview, ReceiptItem
class ReceiptItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'quantity', 'category', 'receipt')
    list_filter = ('category',)
    search_fields = ('name',)
class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 1


class ReceiptExtractionReviewInline(admin.StackedInline):
    model = ReceiptExtractionReview
    can_delete = False
    extra = 0


class ReceiptAdmin(admin.ModelAdmin):
    inlines = [ReceiptItemInline, ReceiptExtractionReviewInline]
    list_display = ('receipt_id', 'user_id', 'purchase_date', 'total_amount', 'subtotal_amount', 'discount_amount', 'store_name', 'status', 'created_at', 'updated_at')
    list_filter = ('status','store_name', 'purchase_date', 'created_at')
    search_fields = ('receipt_id', 'user_id')


admin.site.register(Receipt, ReceiptAdmin)
admin.site.register(ReceiptItem, ReceiptItemAdmin)


@admin.register(ReceiptExtractionReview)
class ReceiptExtractionReviewAdmin(admin.ModelAdmin):
    list_display = ('receipt', 'status', 'overall_confidence', 'approved_by', 'approved_at', 'updated_at')
    list_filter = ('status', 'approved_at', 'updated_at')
    search_fields = ('receipt__receipt_id', 'approved_by')
