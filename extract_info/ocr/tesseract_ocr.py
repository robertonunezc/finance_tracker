# receipts/ocr/tesseract_ocr.py
from dataclasses import dataclass
import cv2
import pytesseract
import numpy as np


@dataclass
class OcrResult:
    text: str
    engine: str = "tesseract"
    lang: str = "spa"


def extract_text_from_receipt(image_path: str, lang: str = "spa") -> OcrResult:
    # Load image
    img = cv2.imread(image_path)

    if img is None:
        raise ValueError("Could not load image")

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    gray = cv2.resize(
        gray,
        None,
        fx=3,
        fy=3,
        interpolation=cv2.INTER_CUBIC
    )
    gray = cv2.medianBlur(gray, 5)

    _, thresh = cv2.threshold(
        gray, 0, 255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )


    # Denoise


    config = (
        "--oem 1 "
        "--psm 6 "
        "-c preserve_interword_spaces=1"
    )

    text = pytesseract.image_to_string(
        thresh,
        lang=lang,
        config=config
    )

    return OcrResult(text=text.strip())
