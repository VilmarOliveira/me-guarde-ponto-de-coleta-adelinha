"""FastFlow API adapter, based on the owner's supplied API documentation.

No POST retries: the documented PIX API has no idempotency key. Exceptions never
contain response bodies, request payloads, card numbers, CVV or credentials.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from urllib.parse import urlsplit
import requests

BASE_URL = "https://fastdepix.space/api/v1"
AMOUNT_CENTS = 3390
PLAN_NAME = "Plano ME GUARDE Mais"
# Current public offer is Pix-only. Preserve Card In data and implementation.
CARD_CHECKOUT_AVAILABLE = False


class GatewayError(Exception):
    def __init__(
        self,
        message="FastFlow temporariamente indisponível. Tente consultar o pagamento novamente.",
        *,
        uncertain=False,
        code="unavailable"
    ):
        super().__init__(message)
        self.uncertain = uncertain
        self.code = code


def enabled(value):
    return str(value).lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    api_key: str
    webhook_secret: str
    origin: str
    active: bool = False
    card_enabled: bool = False
    card_pci_ready: bool = False

    @classmethod
    def from_config(cls, config):
        return cls(
            config.get("FASTFLOW_API_KEY", "").strip(),
            config.get("FASTFLOW_WEBHOOK_SECRET", "").strip(),
            config.get("FASTFLOW_PUBLIC_ORIGIN", "").rstrip("/"),
            enabled(config.get("FASTFLOW_ENABLED", "0")),
            enabled(config.get("FASTFLOW_CARD_ENABLED", "0"))
            and CARD_CHECKOUT_AVAILABLE,
            enabled(config.get("FASTFLOW_CARD_PCI_READY", "0")),
        )

    @property
    def ready(self):
        try:
            parsed = urlsplit(self.origin)
        except ValueError:
            return False
        return bool(
            self.active
            and self.api_key
            and len(self.webhook_secret) >= 32
            and parsed.scheme == "https"
            and parsed.netloc
            and not parsed.username
            and not parsed.query
            and not parsed.fragment
            and parsed.path in ("", "/")
        )

    def callback(self, order_id):
        return self.origin + "/webhooks/fastflow/pedidos/" + order_id


def money_matches(value):
    try:
        amount = Decimal(str(value))
        return amount.is_finite() and amount * 100 == AMOUNT_CENTS
    except (InvalidOperation, ValueError, TypeError):
        return False


def transaction_id(value):
    value = str(value)
    if not re.fullmatch("[1-9][0-9]{0,29}", value):
        raise GatewayError(
            "A FastFlow retornou um identificador inválido.", code="invalid_response"
        )
    return value


class FastFlow:
    def __init__(self, settings, rate_guard=None):
        self.settings = settings
        self.rate_guard = rate_guard

    def request(self, method, path, *, payload=None, params=None):
        if self.rate_guard:
            self.rate_guard(method, path)
        try:
            response = requests.request(
                method,
                BASE_URL + path,
                headers={
                    "Authorization": "Bearer " + self.settings.api_key,
                    "Accept": "application/json",
                },
                json=payload,
                params=params,
                timeout=(5, 15),
                allow_redirects=False,
            )
        except requests.RequestException:
            raise GatewayError(uncertain=method == "POST") from None
        if response.status_code == 402:
            raise GatewayError(
                "Cartão recusado pelo emissor. Confira os dados ou escolha Pix.",
                code="declined",
            )
        if response.status_code == 403 and path.startswith("/card-in"):
            raise GatewayError(
                "Cartão não habilitado na conta FastFlow. O Pix continua disponível.",
                code="card_disabled",
            )
        if response.status_code == 429:
            raise GatewayError(
                "Limite de consultas da FastFlow atingido. Aguarde antes de tentar novamente.",
                code="rate_limited",
            )
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise GatewayError(uncertain=method == "POST")
        if response.status_code in (400, 401, 403, 404, 422):
            raise GatewayError(
                "A FastFlow não autorizou a operação. Confira a configuração e os dados do cadastro.",
                code="rejected",
            )
        try:
            data = response.json()
        except (ValueError, TypeError):
            raise GatewayError(
                uncertain=method == "POST", code="invalid_response"
            ) from None
        if (
            not 200 <= response.status_code < 300
            or not isinstance(data, dict)
            or data.get("success") is not True
        ):
            raise GatewayError(uncertain=method == "POST", code="invalid_response")
        result = data.get("data")
        if not isinstance(result, (dict, list)):
            raise GatewayError(uncertain=method == "POST", code="invalid_response")
        return result

    def provider(self):
        info = self.request("GET", "/auth/me")
        if not isinstance(info, dict) or info.get("api_provider") != "fastflow":
            raise GatewayError(
                "Use uma chave de integração do tipo fastflow. Pagamentos não foram habilitados.",
                code="wrong_provider",
            )
        return True

    def card_available(self):
        if not (self.settings.card_enabled and self.settings.card_pci_ready):
            return False
        self.request("GET", "/card-in/fees")
        return True

    def create(self, order, owner, card=None):
        self.provider()
        callback = self.settings.callback(order["id"])
        if order["method"] == "card":
            if not self.card_available():
                raise GatewayError(
                    "Cartão desabilitado até liberação do Card In e conclusão da configuração PCI. Escolha Pix.",
                    code="card_disabled",
                )
            return self.request(
                "POST",
                "/card-in/charges",
                payload={
                    "amount": 33.90,
                    "installments": 1,
                    "card": card,
                    "payer_name": owner["name"],
                    "payer_document": re.sub(r"\D", "", owner["cpf"]),
                    "payer_email": owner["email"],
                    "external_id": order["id"],
                    "description": PLAN_NAME,
                    "notification_url": callback,
                },
            )
        return self.request(
            "POST",
            "/transactions",
            payload={
                "amount": 33.90,
                "user": {
                    "name": owner["name"],
                    "cpf_cnpj": re.sub(r"\D", "", owner["cpf"]),
                },
                "notification_url": callback,
            },
        )

    def get(self, identifier):
        identifier = transaction_id(identifier)
        data = self.request("GET", "/transactions/" + identifier)
        if (
            not isinstance(data, dict)
            or transaction_id(data.get("id", data.get("transaction_id"))) != identifier
        ):
            raise GatewayError(
                "Identificador divergente na consulta.", code="identity_mismatch"
            )
        return data

    def find(self, order):
        # The order-specific documented callback is the correlation identifier
        # for PIX. A missing match does NOT authorize another create request.
        matches = []
        for page in range(1, 21):
            result = self.request(
                "GET",
                "/transactions",
                params={
                    "date_from": order["created_at"][:10],
                    "payment_method": order["method"],
                    "page": page,
                    "limit": 100,
                },
            )
            if not isinstance(result, dict) or not isinstance(
                result.get("transactions"), list
            ):
                raise GatewayError(code="invalid_response")
            for tx in result["transactions"]:
                if tx.get("notification_url") == self.settings.callback(order["id"]):
                    matches.append(tx)
            pagination = result.get("pagination") or {}
            try:
                total = int(pagination.get("total_pages", 1))
            except (AttributeError, TypeError, ValueError):
                raise GatewayError(code="invalid_response") from None
            if page >= total or len(result["transactions"]) < 100:
                break
        if len(matches) > 1:
            raise GatewayError(
                "Mais de uma transação corresponde ao pedido. Consulte a administração.",
                code="duplicate_remote",
            )
        return matches[0] if matches else None
