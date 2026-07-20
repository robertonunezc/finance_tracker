from django.db import models
import uuid
from pgvector.django import VectorField
STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('needs_review', 'Needs review'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]

REVIEW_STATUS_CHOICES = [
        ('needs_review', 'Needs review'),
        ('approved', 'Approved'),
    ]


class Category(models.TextChoices):
    TOYS = 'toys', 'Juguetes'
    GROCERIES = 'groceries', 'Abarrotes'
    BEVERAGES = 'beverages', 'Bebidas'
    ALCOHOL = 'alcohol', 'Bebidas Alcohólicas'
    DAIRY = 'dairy', 'Lácteos'
    FRUITS = 'fruits', 'Frutas'
    VEGETABLES = 'vegetables', 'Verduras'
    PRODUCE = 'produce', 'Productos Frescos'
    MEAT = 'meat', 'Carnes y Proteínas'
    BAKERY = 'bakery', 'Panadería'
    FROZEN = 'frozen', 'Productos Congelados'
    PANTRY = 'pantry', 'Despensa'
    SNACKS = 'snacks', 'Botanas'
    MEDICATION = 'medication', 'Medicamentos'
    HEALTH = 'health', 'Salud y Bienestar'
    PERSONAL_CARE = 'personal_care', 'Cuidado Personal'
    TOILETRIES = 'toiletries', 'Artículos de Tocador'
    HOUSEHOLD = 'household', 'Suministros del Hogar'
    CLEANING = 'cleaning', 'Productos de Limpieza'
    PAPER_PRODUCTS = 'paper_products', 'Productos de Papel'
    PET_SUPPLIES = 'pet_supplies', 'Suministros para Mascotas'
    BABY_PRODUCTS = 'baby_products', 'Productos para Bebés'
    ELECTRONICS = 'electronics', 'Electrónica'
    RESTAURANT = 'restaurant', 'Restaurante'
    CLOTHING = 'clothing', 'Ropa'
    SCHOOL_SUPPLIES = 'school_supplies', 'Útiles Escolares'
    TRANSPORTATION = 'transportation', 'Transporte'
    ENTERTAINMENT = 'entertainment', 'Entretenimiento'
    UTILITIES = 'utilities', 'Servicios'
    GAS = 'gas', 'Gasolina'
    TAXES = 'taxes', 'Impuestos'
    OTHER = 'other', 'Otro'


CATEGORY_CHOICES = Category.choices
class Receipt(models.Model):
    """Receipt model for storing purchase receipt information."""  
    receipt_id = models.UUIDField(default=uuid.uuid4, unique=True)
    user_id = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, null=True, blank=True, db_index=True)
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

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user_id', 'file_hash'],
                condition=models.Q(file_hash__isnull=False),
                name='unique_receipt_file_hash_per_user',
            ),
        ]
    
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


class ReceiptExtractionReview(models.Model):
    receipt = models.OneToOneField(
        Receipt,
        on_delete=models.CASCADE,
        related_name='extraction_review',
    )
    status = models.CharField(
        max_length=20,
        choices=REVIEW_STATUS_CHOICES,
        default='needs_review',
    )
    overall_confidence = models.FloatField(default=0.0)
    issues = models.JSONField(default=list, blank=True)
    raw_extraction = models.JSONField(default=dict, blank=True)
    corrected_payload = models.JSONField(null=True, blank=True)
    approved_by = models.CharField(max_length=255, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Review for {self.receipt.receipt_id}"

    class Meta:
        indexes = [
            models.Index(fields=['status', '-updated_at'], name='receipt_review_queue_idx'),
        ]
