"""Cópia PDF do contrato histórico aceito."""
import base64, textwrap
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from branding import DOCUMENT_BRANDS, pdf_fonts

def build_contract_pdf(client):
    out=BytesIO(); pdf=canvas.Canvas(out,pagesize=A4); width,height=A4; y=height-45
    version = client['document_brand_version'] if 'document_brand_version' in client.keys() else None
    brand = DOCUMENT_BRANDS.get(version)
    branded = bool(brand)
    normal_font, bold_font = pdf_fonts(version) if branded else ('Helvetica', 'Helvetica-Bold')
    def brand_page():
        nonlocal y
        if branded:
            from reportlab.lib.colors import HexColor
            pdf.setFillColor(HexColor(brand["orange"])); pdf.rect(0,height-12,width,12,fill=1,stroke=0)
            pdf.setFillColor(HexColor(brand["text"]))
            logo = brand["asset_root"] / "logo-original.png"
            if logo.is_file() and "document_logo_present" in client.keys() and client["document_logo_present"]:
                pdf.drawImage(str(logo),width-105,height-100,width=60,height=60,preserveAspectRatio=True,mask='auto')
                y=height-115
    brand_page()
    def line(text,bold=False):
        nonlocal y
        for wrapped in textwrap.wrap(str(text),100) or ['']:
            if y<60: pdf.showPage(); y=height-45; brand_page()
            pdf.setFont(bold_font if bold else normal_font,9); pdf.drawString(45,y,wrapped); y-=12
    line('PONTO DE COLETA ADELINHA — CONTRATO ASSINADO',True)
    if branded: line('ME GUARDE — COMPROU? DEIXA COM A GENTE.',True)
    code = client["contract_public_id"] if "contract_public_id" in client.keys() else None
    code = code or client["public_id"]
    line(f"Contratante: {client['name']} | CPF: {client['cpf']} | Código: {code}")
    line(f"Versão: {client['terms_version']} | Aceite: {client['accepted_at']}")
    for paragraph in (client['terms_text'] or 'Texto histórico não registrado').splitlines(): line(paragraph, paragraph[:2].rstrip('.').isdigit() or paragraph.isupper())
    if y<150: pdf.showPage(); y=height-45; brand_page()
    line('Assinatura registrada:',True)
    signature=client['signature']
    if signature and signature.startswith('data:image/png;base64,'):
        pdf.drawImage(ImageReader(BytesIO(base64.b64decode(signature.split(',',1)[1]))),45,y-95,width=260,height=90,preserveAspectRatio=True)
    else: line('Não registrado')
    pdf.save(); out.seek(0); return out


def build_current_contract_pdf(text, version):
    """Unsigned current version, separate from immutable signed snapshots."""
    from html import escape
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import ParagraphStyle
    from branding import BRAND_VERSION
    normal, bold = pdf_fonts(BRAND_VERSION)
    output = BytesIO()
    style = ParagraphStyle('contract', fontName=normal, fontSize=10, leading=15, spaceAfter=8)
    heading = ParagraphStyle('heading', parent=style, fontName=bold, fontSize=12, leading=17)
    story = [Paragraph(escape(version) + ' — contrato vigente, sem aceite', heading), Spacer(1, 10)]
    for paragraph in text.split('\n\n'):
        story.append(Paragraph(escape(paragraph).replace('\n', '<br/>'), style))
    SimpleDocTemplate(output, pagesize=A4, rightMargin=45, leftMargin=45, topMargin=45, bottomMargin=45).build(story)
    output.seek(0)
    return output
