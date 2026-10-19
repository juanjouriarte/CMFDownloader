"""Article 12 Annex 1 codes, CMF technical sheet pages 3–5.

Keep unknown future codes verbatim; do not mistake disclosures for unique trades.
"""
SOURCE_URL = 'https://www.cmfchile.cl/portal/principal/623/articles-16247_Transacciones_Ficha_Tecnica.pdf'
CODES = {
    'relationship': {
        'CO': 'Accionista controlador obligado a informar',
        'AM': 'Accionista no controlador obligado a informar',
        'DI': 'Director', 'PD': 'Presidente del directorio',
        'GG': 'Gerente general', 'GF': 'Gerente de finanzas o equivalente',
        'GA': 'Gerente de otra área', 'LI': 'Liquidador',
        'AD': 'Administrador', 'EJ': 'Otro ejecutivo principal',
    },
    'transaction_type': {
        'A': 'Adquisición', 'E': 'Enajenación',
        'SVPE': 'Suscripción de valores de primera emisión', 'DP': 'Dación en pago',
        'SCM': 'Sucesión por causa de muerte', 'D': 'Donación', 'O': 'Otro',
    },
    'security_type': {
        'ACC': 'Acciones', 'OTRC': 'Otros títulos representativos de capital',
        'TD': 'Títulos de deuda', 'OTRO': 'Otros valores',
    },
    'entity_status': {'VI': 'Entidad vigente', 'NV': 'Entidad no vigente'},
}
