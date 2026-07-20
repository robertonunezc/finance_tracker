from pathlib import Path

from django import forms


class ReceiptUploadForm(forms.Form):
    document = forms.FileField(
        label="Receipt or statement file",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/*,application/pdf",
                "class": "form-control",
            }
        ),
    )

    def clean_document(self):
        document = self.cleaned_data["document"]
        file_type = infer_receipt_upload_file_type(
            document.name,
            getattr(document, "content_type", ""),
        )
        if file_type is None:
            raise forms.ValidationError("Upload a receipt image or PDF bank statement.")
        self.cleaned_data["file_type"] = file_type
        return document


def infer_receipt_upload_file_type(filename: str, content_type: str | None) -> str | None:
    suffix = Path(filename or "").suffix.lower()
    normalized_content_type = (content_type or "").lower()

    if normalized_content_type == "application/pdf" or suffix == ".pdf":
        return "pdf"
    if normalized_content_type.startswith("image/") or suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return "image"
    return None
