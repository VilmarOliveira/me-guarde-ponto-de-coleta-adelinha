"""Local payment settings; no remote payment provider is configured."""

from dataclasses import dataclass

AMOUNT_CENTS = 3390


class PaymentError(ValueError):
    def __init__(self, message, **details):
        super().__init__(message)
        self.code = details.get("code")


@dataclass(frozen=True)
class LocalSettings:
    origin: str = ""
    ready: bool = True

    @classmethod
    def from_config(cls, config):
        return cls(config.get("SITE_PUBLIC_ORIGIN", "").rstrip("/"))

    def callback(self, order_id):
        return "local:" + order_id
