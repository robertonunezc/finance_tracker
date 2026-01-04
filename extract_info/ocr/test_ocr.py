# test_ocr.py
from extract_info.ocr.tesseract_ocr import extract_text_from_receipt
image_path = "TicketGrocery.jpg"
result = extract_text_from_receipt(image_path, lang="spa")
print("---- OCR OUTPUT ----")
print(result.text)
