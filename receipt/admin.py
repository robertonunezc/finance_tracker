from django.contrib import admin


# Register your models here.

from .models import Receipt, ReceiptItem
class ReceiptItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'price', 'quantity', 'category', 'receipt')
    list_filter = ('category',)
    search_fields = ('name',)
class ReceiptItemInline(admin.TabularInline):
    model = ReceiptItem
    extra = 1


class ReceiptAdmin(admin.ModelAdmin):
    inlines = [ReceiptItemInline]
    list_display = ('receipt_id', 'user_id', 'purchase_date', 'total_amount', 'status', 'created_at', 'updated_at')
    list_filter = ('status', 'purchase_date', 'created_at')
    search_fields = ('receipt_id', 'user_id')


admin.site.register(Receipt, ReceiptAdmin)
admin.site.register(ReceiptItem, ReceiptItemAdmin)