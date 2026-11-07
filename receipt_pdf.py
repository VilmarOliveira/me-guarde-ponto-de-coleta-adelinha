"""Geração do comprovante histórico de retirada em PDF."""
import base64
from datetime import datetime
from io import BytesIO

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from core import TZ
from branding import DOCUMENT_BRANDS, pdf_fonts


def _value(row, key):
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _display(value, money=False):
    if value is None or value == "":
        return "Não registrado"
    return f"R$ {float(value):.2f}".replace(".", ",") if money else str(value)


def _local_datetime(value):
    if not value:
        return "Não registrado"
    return datetime.fromisoformat(value).astimezone(TZ).strftime("%d/%m/%Y %H:%M")


def _document(withdrawal):
    document_type = _value(withdrawal, "document_type")
    number = _value(withdrawal, "receiver_document")
    if not document_type and not number:
        return "Não registrado"
    return " ".join(part for part in (_display(document_type), _display(number)) if part != "Não registrado")


class _ReceiptCanvas:
    """Desenha texto com quebra automática e cria páginas somente quando necessário."""

    def __init__(self, output, title, address, brand_version=None, logo_present=False):
        self.brand = DOCUMENT_BRANDS.get(brand_version)
        self.branded = bool(self.brand)
        self.logo_present = logo_present
        self.font, self.bold_font = pdf_fonts(brand_version) if self.branded else ("Helvetica", "Helvetica-Bold")
        self.pdf = canvas.Canvas(output, pagesize=A4, pageCompression=0)
        self.width, self.height = A4
        self.margin = 45
        self.address = address
        self.pdf.setTitle(title)
        self._header()

    def _header(self):
        if self.branded:
            from reportlab.lib.colors import HexColor
            self.pdf.setFillColor(HexColor(self.brand["orange"]))
            self.pdf.rect(0, self.height - 12, self.width, 12, fill=1, stroke=0)
            self.pdf.setFillColor(HexColor(self.brand["text"]))
            logo = self.brand["asset_root"] / "logo-original.png"
            if self.logo_present and logo.is_file():
                self.pdf.drawImage(str(logo), self.width - 105, self.height - 100,
                                   width=60, height=60, preserveAspectRatio=True, mask='auto')
        self.pdf.setFont(self.bold_font, 16)
        self.pdf.drawString(self.margin, self.height - 45, "PONTO DE COLETA ADELINHA")
        self.pdf.setFont(self.font, 9)
        self.pdf.drawString(self.margin, self.height - 62, "CNPJ: 38.145.273/0001-05")
        self.pdf.drawString(self.margin, self.height - 76, self.address)
        self.pdf.drawString(self.margin, self.height - 90, "WhatsApp: (16) 98800-4966")
        if self.branded:
            self.pdf.setFont(self.bold_font, 8)
            self.pdf.drawString(self.margin, self.height - 106, "ME GUARDE — COMPROU? DEIXA COM A GENTE.")
        self.y = self.height - (128 if self.branded else 112)

    def _new_page(self):
        self.pdf.showPage()
        self._header()

    def ensure(self, height):
        if self.y - height < self.margin:
            self._new_page()

    def line(self, text, bold=False, size=9, leading=13):
        font = self.bold_font if bold else self.font
        available = self.width - 2 * self.margin
        words = []
        for word in str(text).split():
            if stringWidth(word, font, size) <= available:
                words.append(word)
                continue
            chunk = ""
            for character in word:
                if chunk and stringWidth(chunk + character, font, size) > available:
                    words.append(chunk)
                    chunk = character
                else:
                    chunk += character
            if chunk:
                words.append(chunk)
        lines, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if not current or stringWidth(candidate, font, size) <= available:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current or " ")
        self.ensure(len(lines) * leading)
        self.pdf.setFont(font, size)
        for wrapped in lines:
            self.pdf.drawString(self.margin, self.y, wrapped)
            self.y -= leading

    def signature(self, value):
        self.ensure(125)
        self.line("Assinatura de quem retirou:", bold=True)
        if value and value.startswith("data:image/png;base64,"):
            image_data = base64.b64decode(value.split(",", 1)[1])
            self.pdf.drawImage(ImageReader(BytesIO(image_data)), self.margin, self.y - 92,
                               width=260, height=88, preserveAspectRatio=True, anchor="sw")
            self.y -= 98
        else:
            self.line("Não registrado")


def build_receipt_pdf(withdrawal, packages, address):
    """Gera o PDF apenas com snapshots persistidos no momento da retirada."""
    output = BytesIO()
    package_id = _display(_value(withdrawal, "package_public_id"))
    receipt_code = f"CMPPCA-{_value(withdrawal, 'id')}" if _value(withdrawal, "id") else "Não registrado"
    document = _ReceiptCanvas(output, f"Comprovante {receipt_code}", address,
                              brand_version=_value(withdrawal, "document_brand_version"),
                              logo_present=bool(_value(withdrawal, "document_logo_present")))
    document.line(f"Comprovante: {receipt_code}", bold=True)
    final_total = _value(withdrawal, "final_total")
    if final_total is None:
        final_total = _value(withdrawal, "total_paid")
    surcharge = _value(withdrawal, "surcharge_amount")
    discount = _value(withdrawal, "discount_amount")
    fields = (
        ("Rastreio registrado", _display(_value(withdrawal, "snapshot_tracking"))),
        ("Categoria registrada", _display(_value(withdrawal, "snapshot_category"))),
        ("Aviso de chegada registrado", _local_datetime(_value(withdrawal, "snapshot_notified_at"))),
        ("Cliente registrado na retirada", f"{_display(_value(withdrawal, 'snapshot_client_name'))} — {_display(_value(withdrawal, 'snapshot_client_public_id'))}"),
        ("Data e hora", _local_datetime(_value(withdrawal, "picked_at"))),
        ("Quem retirou", _display(_value(withdrawal, "receiver_name"))),
        ("Documento", _document(withdrawal)),
        ("Valor base deste pacote", _display(_value(withdrawal, "service_amount"), True)),
        ("Dias de atraso", _display(_value(withdrawal, "late_days"))),
        ("Taxa de atraso", _display(_value(withdrawal, "late_amount"), True)),
        ("Acréscimo manual", _display(surcharge if surcharge is not None else 0, True)),
        ("Motivo do acréscimo", _display(_value(withdrawal, "surcharge_reason")) if surcharge is None or float(surcharge) > 0 else "Não se aplica"),
        ("Desconto aplicado", _display(discount if discount is not None else 0, True)),
        ("Saldo pago nesta retirada" if _value(withdrawal,"prepaid_amount") else "Total final pago", _display(final_total, True)),
        ("Tipo de Pagamento", _display(_value(withdrawal, "payment_method"))),
    )
    if _value(withdrawal,"prepaid_amount"):
        fields += (("Pago antecipadamente neste pacote",_display(_value(withdrawal,"prepaid_amount"),True)),)
    if _value(withdrawal,"external_transaction_id"):
        fields += (("Transação Fast",str(_value(withdrawal,"external_transaction_id"))),)
    if _value(withdrawal, "payment_method") in ("Pix","Pix AI"):
        fields += (
            ("Titular Pix registrado", _display(_value(withdrawal, "pix_receiver_snapshot"))),
            ("Chave Pix registrada", _display(_value(withdrawal, "pix_key_snapshot"))),
        )
    for label, value in fields:
        document.line(f"{label}: {value}")

    document.signature(_value(withdrawal, "signature"))
    document.pdf.save()
    output.seek(0)
    return output
