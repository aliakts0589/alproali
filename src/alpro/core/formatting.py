"""Locale-aware number formatting (Turkish first: 1.234.567,89)."""
from __future__ import annotations

CURRENCY_SYMBOLS = {"TRY": "₺", "USD": "$", "EUR": "€"}


def fmt_num(value: float, decimals: int = 2, locale: str = "tr") -> str:
    s = f"{value:,.{decimals}f}"
    if locale == "tr":
        s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return s


def fmt_money(value: float, currency: str = "TRY", decimals: int = 2, locale: str = "tr") -> str:
    symbol = CURRENCY_SYMBOLS.get(currency.upper(), currency.upper() + " ")
    if locale == "tr":
        return f"{fmt_num(value, decimals, locale)} {symbol}".strip()
    return f"{symbol}{fmt_num(value, decimals, locale)}"


def fmt_pct(value: float, decimals: int = 2, locale: str = "tr", signed: bool = True) -> str:
    sign = "+" if signed and value > 0 else ""
    if locale == "tr":
        return f"%{sign}{fmt_num(value, decimals, locale)}"
    return f"{sign}{fmt_num(value, decimals, locale)}%"
