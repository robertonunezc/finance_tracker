from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from receipt.models import Category


@dataclass
class ReceiptItem:
    name: str
    line_total: float
    quantity: Optional[int] = 1
    unit_price: Optional[float] = None
    category: Optional[str] = 'other'
    category_confidence: Optional[float] = None
    embedding: Optional[List[float]] = None 

@dataclass
class ReceiptData:
    user_id: str
    image_url: str
    purchase_date: Optional[datetime] = None
    total_amount: Optional[Decimal] = None
    status: Optional[str] = 'pending'
    items: Optional[List[ReceiptItem]] = None
    source_type: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReceiptLookupResult:
    receipt_id: str
    user_id: str
    image_url: str
    status: str
    created: bool
    file_hash: Optional[str] = None
    source_type: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiptUploadRequest:
    user_id: str
    source_file_path: str
    original_filename: str
    file_type: str
    source_type: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReceiptUploadResult:
    receipt_id: str
    user_id: str
    image_url: str
    status: str
    action: str
    file_hash: str
    file_type: str
    should_enqueue: bool
    source_type: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)
