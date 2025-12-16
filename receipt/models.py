from django.db import models
import uuid


class Receipt(models.Model):
    """Receipt model for storing purchase receipt information."""
    
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    
    receipt_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user_id = models.CharField(max_length=255)
    purchase_date = models.DateTimeField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    image_url = models.URLField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Receipt {self.receipt_id}"


class ReceiptItem(models.Model):
    """ReceiptItem model for storing individual items in a receipt."""
    
    CATEGORY_CHOICES = [
        ('groceries', 'Groceries'),
        ('beverages', 'Beverages'),
        ('dairy', 'Dairy'),
        ('produce', 'Produce'),
        ('meat', 'Meat & Protein'),
        ('bakery', 'Bakery'),
        ('frozen', 'Frozen Foods'),
        ('pantry', 'Pantry Staples'),
        ('snacks', 'Snacks'),
        ('medication', 'Medication'),
        ('health', 'Health & Wellness'),
        ('personal_care', 'Personal Care'),
        ('toiletries', 'Toiletries'),
        ('household', 'Household Supplies'),
        ('cleaning', 'Cleaning Products'),
        ('paper_products', 'Paper Products'),
        ('pet_supplies', 'Pet Supplies'),
        ('baby_products', 'Baby Products'),
        ('electronics', 'Electronics'),
        ('restaurant', 'Restaurant'),
        ('clothing', 'Clothing'),
        ('school_supplies', 'School Supplies'),
        ('transportation', 'Transportation'),
        ('entertainment', 'Entertainment'),
        ('utilities', 'Utilities'),
        ('gas', 'Gas'),
        ('taxes', 'Taxes'),
        ('other', 'Other'),
    ]
    
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=255)
    price = models.FloatField()
    quantity = models.IntegerField(default=1)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default='other')
    
    def __str__(self):
        return f"{self.name} ({self.receipt.receipt_id})"
