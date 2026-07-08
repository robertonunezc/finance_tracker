from django.db import models
import uuid
from pgvector.django import VectorField
STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]


class Category(models.TextChoices):
    GROCERIES = 'groceries', 'Groceries'
    BEVERAGES = 'beverages', 'Beverages'
    ALCOHOL = 'alcohol', 'Alcohol'
    DAIRY = 'dairy', 'Dairy'
    FRUITS = 'fruits', 'Fruits'
    VEGETABLES = 'vegetables', 'Vegetables'
    PRODUCE = 'produce', 'Produce'
    MEAT = 'meat', 'Meat & Protein'
    BAKERY = 'bakery', 'Bakery'
    FROZEN = 'frozen', 'Frozen Foods'
    PANTRY = 'pantry', 'Pantry Staples'
    SNACKS = 'snacks', 'Snacks'
    MEDICATION = 'medication', 'Medication'
    HEALTH = 'health', 'Health & Wellness'
    PERSONAL_CARE = 'personal_care', 'Personal Care'
    TOILETRIES = 'toiletries', 'Toiletries'
    HOUSEHOLD = 'household', 'Household Supplies'
    CLEANING = 'cleaning', 'Cleaning Products'
    PAPER_PRODUCTS = 'paper_products', 'Paper Products'
    PET_SUPPLIES = 'pet_supplies', 'Pet Supplies'
    BABY_PRODUCTS = 'baby_products', 'Baby Products'
    ELECTRONICS = 'electronics', 'Electronics'
    RESTAURANT = 'restaurant', 'Restaurant'
    CLOTHING = 'clothing', 'Clothing'
    SCHOOL_SUPPLIES = 'school_supplies', 'School Supplies'
    TRANSPORTATION = 'transportation', 'Transportation'
    ENTERTAINMENT = 'entertainment', 'Entertainment'
    UTILITIES = 'utilities', 'Utilities'
    GAS = 'gas', 'Gas'
    TAXES = 'taxes', 'Taxes'
    OTHER = 'other', 'Other'


CATEGORY_CHOICES = Category.choices
class Receipt(models.Model):
    """Receipt model for storing purchase receipt information."""  
    receipt_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user_id = models.CharField(max_length=255)
    purchase_date = models.DateTimeField()
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    subtotal_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    store_name = models.CharField(max_length=255, null=True, blank=True)
    image_url = models.URLField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    extracted_text = models.TextField(null=True, blank=True)
    extraction_result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"Receipt {self.receipt_id}"


class ReceiptItem(models.Model):
    """ReceiptItem model for storing individual items in a receipt."""
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=255)
    price = models.FloatField()
    quantity = models.IntegerField(default=1)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=Category.OTHER)
    embedding = VectorField(dimensions=1536,null=True, blank=True)
    
    def __str__(self):
        return f"{self.name} ({self.receipt.receipt_id})"
