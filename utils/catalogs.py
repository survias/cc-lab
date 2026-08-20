from __future__ import annotations


DOCUMENT_TYPES = {
    22: "Boleta de honorarios electrónica",
    33: "Factura electrónica",
    34: "Factura exenta electrónica",
    46: "Factura de compra electrónica",
    56: "Nota de débito electrónica",
    61: "Nota de crédito electrónica",
}

COST_CATEGORIES = {
    100: "EPC - Contractor",
    200: "Maintenance",
    300: "Operations",
    400: "SPV",
    500: "MOP",
    600: "Financing Cost",
    700: "Insurance & Guarantee",
    800: "Tax",
    900: "SPV Others",
    1000: "EPC - SPV",
}

COST_SUBCATEGORIES = {
    100: {
        101: "Advance Payment",
        102: "PID (Detailed Engineering Project)",
        103: "ITS (Intelligent Transport Systems)",
        104: "Construction Toll Plaza",
        105: "Expropriation & Land Acquisition",
        106: "Utility Relocation Works",
        107: "Environmental Studies",
        108: "EPC Works",
        109: "Contract Management",
        # Código heredado que aún puede aparecer en referencias históricas.
        110: "Construction Department Others (legacy)",
    },
    200: {
        201: "Routine Maintenance",
        202: "Major Maintenances",
        203: "RM-MM",
        204: "Maintenance Studies",
        205: "Maintenance Others Works",
    },
    300: {301: "Operations", 302: "Backoffice"},
    400: {
        401: "Administration Department",
        402: "Others",
        403: "Legal Department",
        404: "HR Department",
        405: "Finance Department",
        406: "Economic Department",
        407: "Utilities",
        408: "IGYC Costs",
    },
    500: {
        501: "Goods & Rights",
        502: "Pre-existing Infrastructure",
        503: "Toll Revenue Sharing",
        504: "Legacy MOP center",
        505: "Others MOP",
    },
    600: {601: "Long-term Loan", 602: "Short-term Loan", 603: "Other Financing Fees"},
    700: {701: "Insurance", 702: "Guarantee"},
    800: {801: "VAT", 802: "Income Tax", 803: "Municipal Patent", 804: "Other Tax"},
    900: {901: "SPV Other", 902: "IT", 903: "Technical Advisory", 904: "PMO"},
    1000: {
        1001: "IF Office Expenses",
        1002: "Citizen Studies",
        1003: "Construction Department Works",
    },
}

PAYMENT_STATUSES = [
    "Pagado",
    "No pagado",
    "Pago sin documento",
    "Revisar cruce",
    "Nota de crédito",
    "Anulada por NC",
]

PENDING_MODULES = {
    "Carga de datos": "Se habilitará después de implementar cargas idempotentes y auditables.",
}


def document_type_label(document_type: int) -> str:
    return DOCUMENT_TYPES.get(document_type, f"Tipo {document_type} - no clasificado")


def category_label(category_code: int | float | None) -> str:
    if category_code is None:
        return "Sin categoría"
    try:
        code = int(category_code)
    except (TypeError, ValueError):
        return "Sin categoría"
    return COST_CATEGORIES.get(code, f"Categoría {code}")


def subcategory_label(category_code: int | float | None, subcategory_code: int | float | None) -> str:
    if subcategory_code is None:
        return "Sin subcategoría"
    try:
        category = int(category_code)
        subcategory = int(subcategory_code)
    except (TypeError, ValueError):
        return "Sin subcategoría"
    return COST_SUBCATEGORIES.get(category, {}).get(subcategory, f"Subcategoría {subcategory}")
