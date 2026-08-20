from __future__ import annotations

from dataclasses import dataclass

from utils.catalogs import document_type_label


@dataclass(frozen=True)
class DocumentEconomicRule:
    document_type: int
    label: str
    sign: int | None
    classified: bool


def document_rule(document_type: int) -> DocumentEconomicRule:
    if document_type in (33, 34):
        return DocumentEconomicRule(document_type, document_type_label(document_type), 1, True)
    if document_type == 61:
        return DocumentEconomicRule(document_type, document_type_label(document_type), -1, True)
    return DocumentEconomicRule(document_type, document_type_label(document_type), None, False)


def economic_effect(document_type: int, amount: float | int | None) -> float:
    numeric_amount = float(amount or 0)
    rule = document_rule(document_type)
    if rule.sign == 1:
        return abs(numeric_amount)
    if rule.sign == -1:
        return -abs(numeric_amount)
    return numeric_amount


def normalize_rut_search(value: str | None) -> str:
    if not value:
        return ""
    original = value.strip().upper()
    cleaned = "".join(char for char in original if char.isdigit() or char == "K")
    if "-" in original and len(cleaned) > 1:
        return cleaned[:-1]
    return cleaned


def normalize_folio(value: str | int | None) -> str:
    if value is None:
        return ""
    text = str(value).strip().upper().replace(" ", "")
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text
