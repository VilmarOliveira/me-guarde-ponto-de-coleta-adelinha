"""New, owner-scoped plan receipts. Prior records are never rewritten."""

from io import BytesIO
from html import escape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from branding import BRAND_VERSION, pdf_fonts
from plan_service import parse


def build(order, person, cycle, method, actor=None):
    out = BytesIO()
    normal, bold = pdf_fonts(BRAND_VERSION)
    style = getSampleStyleSheet()["BodyText"]
    style.fontName = normal
    style.fontSize = 10
    style.leading = 16
    confirmed = order["manual_paid_at"] or order["paid_at"]
    lines = [
        "PONTO DE COLETA ADELINHA",
        "ME GUARDE — COMPROU? DEIXA COM A GENTE.",
        "Comprovante do Plano ME GUARDE Mais",
        "Cliente: " + person["name"] + " — " + (person["public_id"] or ""),
        "Pedido: " + order["id"],
        "Modalidade: " + method,
        "Valor: R$ " + ("%.2f" % (order["amount_cents"] / 100)).replace(".", ","),
        "Confirmação: "
        + (
            parse(confirmed).strftime("%d/%m/%Y %H:%M")
            if confirmed
            else "Pagamento não confirmado / registro de participação"
        ),
        "Origem: "
        + (
            "conferência manual pelo ponto"
            if order["manual_paid_at"]
            else (
                "registro financeiro anterior"
                if order["paid_at"]
                else "histórico administrativo sem confirmação financeira"
            )
        ),
        "Transação externa: " + (order["transaction_id"] or "Não se aplica"),
    ]
    if cycle:
        lines += [
            "Período: "
            + parse(cycle["starts_at"]).strftime("%d/%m/%Y %H:%M")
            + " até "
            + parse(cycle["ends_at"]).strftime("%d/%m/%Y %H:%M"),
            "10 créditos; pequeno utiliza 1, grande utiliza 2. Créditos não acumulam.",
        ]
    if actor:
        lines.append(
            (
                "Pagamento conferido por: "
                if order["manual_paid_at"]
                else "Solicitação registrada por: "
            )
            + actor
        )
    content = []
    for line in lines:
        content += [Paragraph(escape(line), style), Spacer(1, 10)]
    SimpleDocTemplate(
        out, title="Comprovante do plano", author="Ponto de Coleta Adelinha"
    ).build(content)
    out.seek(0)
    return out
