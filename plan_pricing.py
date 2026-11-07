"""Current offer price; issued orders remain immutable financial snapshots."""

from decimal import Decimal, InvalidOperation

DEFAULT = 3390


def current(db):
    return int(
        db.execute("SELECT monthly_price_cents FROM settings WHERE id=1").fetchone()[
            "monthly_price_cents"
        ]
    )


def money(cents):
    return f"{cents/100:.2f}".replace(".", ",")


def parse(value):
    try:
        number = Decimal(str(value).strip().replace(",", "."))
        if (
            not number.is_finite()
            or number <= 0
            or number.as_tuple().exponent < -2
            or number * 100 > 2147483647
        ):
            raise ValueError()
        return int(number * 100)
    except (ValueError, InvalidOperation):
        raise ValueError(
            "Informe uma mensalidade maior que zero, com até duas casas decimais, dentro do limite de armazenamento."
        )
