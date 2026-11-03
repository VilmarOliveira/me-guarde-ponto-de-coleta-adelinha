"""Regras de negócio sem dependências externas.

Manter os cálculos aqui permite testá-los mesmo em um ambiente sem Flask.
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

TZ = ZoneInfo("America/Sao_Paulo")
SMALL_PACKAGE_PRICE=4.90
LARGE_PACKAGE_PRICE=9.90
SMALL_PACKAGE_MAX_SUM=80
LARGE_PACKAGE_MAX_SUM=150
SMALL_PACKAGE_MAX_WEIGHT=10
LARGE_PACKAGE_MAX_WEIGHT=20


def valid_cpf(value):
    """Valida a estrutura e os dois dígitos verificadores de um CPF."""
    numbers = "".join(character for character in str(value) if character.isdigit())
    if len(numbers) != 11 or len(set(numbers)) == 1:
        return False
    for length in (9, 10):
        total = sum(int(numbers[index]) * (length + 1 - index) for index in range(length))
        digit = 11 - total % 11
        if digit >= 10:
            digit = 0
        if int(numbers[length]) != digit:
            return False
    return True


def package_arrival_message(client_name, package_id, tracking, service_value):
    """Monta o aviso de chegada com quebras de linha e moeda brasileira."""
    formatted_value = f"R$ {float(service_value):.2f}".replace(".", ",")
    return (
        f"Olá, {client_name}! 😊\n\n"
        f"Seu pacote {package_id}, com rastreio {tracking}, chegou ao Ponto de Coleta Adelinha!\n\n"
        f"O valor do serviço de armazenamento é de {formatted_value}. Você tem 4 dias corridos para "
        "retirar, contando hoje como o primeiro dia.\n\n"
        "A partir do 5º dia, será cobrada uma taxa adicional de R$ 0,50 por dia, por pacote, até a retirada.\n\n"
        "Agradecemos pela compreensão!"
    )


def calculate_discount(total, amount_text="", percent_text=""):
    """Valida uma modalidade de desconto e calcula totais com duas casas."""
    amount_text, percent_text = str(amount_text).strip(), str(percent_text).strip()
    if amount_text and percent_text:
        raise ValueError("Use somente desconto em reais ou desconto em porcentagem.")
    original = Decimal(str(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not amount_text and not percent_text:
        return None, None, Decimal("0.00"), original, original
    try:
        entered = Decimal((amount_text or percent_text).replace(",", "."))
    except InvalidOperation:
        raise ValueError("Informe um desconto numérico válido.")
    if not entered.is_finite():
        raise ValueError("Informe um desconto numérico válido.")
    if amount_text:
        if entered < 0 or entered > original:
            raise ValueError("O desconto em reais deve estar entre zero e o total da retirada.")
        discount = entered.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        mode = "valor"
    else:
        if entered < 0 or entered > 100:
            raise ValueError("O desconto em porcentagem deve estar entre 0% e 100%.")
        discount = (original * entered / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        mode = "porcentagem"
    final = max(Decimal("0.00"), original - discount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return mode, entered, discount, original, final


def validate_surcharge(amount_text="0", reason=""):
    """Valida um acréscimo manual, separado da taxa automática de atraso."""
    text = str(amount_text or "0").strip().replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError("Informe um acréscimo monetário válido.")
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise ValueError("O acréscimo deve ser não negativo e ter no máximo duas casas decimais.")
    amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    clean_reason = str(reason).strip()
    if amount > 0 and not clean_reason:
        raise ValueError("Informe o motivo do acréscimo.")
    if len(clean_reason) > 300:
        raise ValueError("O motivo do acréscimo deve ter no máximo 300 caracteres.")
    return amount, clean_reason or None


def classify_package(width: float, height: float, length: float, weight: float):
    """Retorna (categoria, preço) ou levanta ValueError para pacote recusado."""
    dimensions = (width, height, length)
    if min(dimensions) <= 0 or weight <= 0:
        raise ValueError("Medidas e peso devem ser maiores que zero.")
    dimension_sum = sum(dimensions)
    if dimension_sum > LARGE_PACKAGE_MAX_SUM or weight > LARGE_PACKAGE_MAX_WEIGHT:
        raise ValueError("Limite de 150 cm na soma e 20 kg.")
    if dimension_sum <= SMALL_PACKAGE_MAX_SUM and weight <= SMALL_PACKAGE_MAX_WEIGHT:
        return "Pequeno", SMALL_PACKAGE_PRICE
    return "Grande", LARGE_PACKAGE_PRICE


def calculate_fees(base_price, notified_at=None, picked_at=None, today: date | None = None):
    """Calcula atraso por datas locais; o dia do aviso é o primeiro dos quatro."""
    base = float(base_price)
    late_days = 0
    if notified_at:
        notice_date = datetime.fromisoformat(notified_at).astimezone(TZ).date()
        last_free_date = notice_date + timedelta(days=3)
        reference = today or (
            datetime.fromisoformat(picked_at).astimezone(TZ).date()
            if picked_at
            else datetime.now(TZ).date()
        )
        late_days = max(0, (reference - last_free_date).days)
    late_fee = late_days * 0.5
    return {"base": base, "late_days": late_days, "late_fee": late_fee, "total": base + late_fee}


def _emv_field(identifier, value):
    length = len(value.encode("utf-8"))
    if length > 99:
        raise ValueError("Campo Pix excede o limite técnico de 99 bytes.")
    return f"{identifier}{length:02d}{value}"


def pix_payload(key, receiver_name, amount):
    """Gera um BR Code Pix estático com CRC16-CCITT."""
    if not receiver_name or amount <= 0:
        raise ValueError("Nome do recebedor e valor são obrigatórios.")
    receiver = unicodedata.normalize("NFKD", receiver_name).encode("ascii", "ignore").decode().strip().upper()[:25]
    if not receiver or not key or not key.isascii() or len(key) > 77:
        raise ValueError("Nome ou chave Pix inválidos para o formato Pix.")
    merchant = _emv_field("00", "BR.GOV.BCB.PIX") + _emv_field("01", key)
    payload = "".join((_emv_field("00", "01"), _emv_field("26", merchant), _emv_field("52", "0000"),
        _emv_field("53", "986"), _emv_field("54", f"{amount:.2f}"), _emv_field("58", "BR"),
        _emv_field("59", receiver), _emv_field("60", "FRANCA"), _emv_field("62", _emv_field("05", "***")), "6304"))
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return payload + f"{crc:04X}"
