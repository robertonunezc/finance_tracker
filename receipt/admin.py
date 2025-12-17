from django.contrib import admin


# Register your models here.

from .models import Receipt, ReceiptItem

admin.site.register(Receipt)
admin.site.register(ReceiptItem)