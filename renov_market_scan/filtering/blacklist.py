"""Reject accessories, parts and junk listings.

Two lists, because some words are ambiguous. 'capinha' only ever means a case,
but 'bateria' appears in the majority of legitimate used-iPhone titles as
'Saude de bateria 90%'. A literal blacklist would discard the best part of the
sample, so ambiguous terms are checked separately, after the model and capacity
rules have already confirmed the advert is about the right device.
"""

from renov_market_scan.filtering.text import normalize_text

ACCESSORY_OR_PART = "acessorio_ou_peca"
LIKELY_PART = "peca_provavel"

# Unambiguous. A title containing any of these is never the phone itself.
HARD_TERMS: tuple[str, ...] = (
    "capa",
    "capinha",
    "case",
    "pelicula",
    "vidro",
    "frontal",
    "touch",
    "placa",
    "flex",
    "conector",
    "carcaca",
    "aro",
    "tampa",
    "camera traseira",
    "alto falante",
    "alto-falante",
    "botao",
    "pecas",
    "sucata",
    "para retirada",
    "nao liga",
    "sem funcionar",
    "replica",
    "clone",
    "similar",
    "generico",
)

# Ambiguous terms mapped to the markers that exempt them. If any marker is
# present the term is describing the phone's condition or spec, not a part.
#
# 'chip', 'carregador', 'fone', 'cabo', 'capa', 'suporte' and 'vidro' moved
# here from HARD_TERMS: a legitimate advert for the phone itself commonly
# mentions them as an included accessory or a built-in feature ("Dual Chip",
# "acompanha carregador original", "brinde: capinha e pelicula"), and a hard
# reject discarded that whole advert instead of just noting the accessory.
CONTEXTUAL_TERMS: dict[str, tuple[str, ...]] = {
    "bateria": ("saude", "%", "capacidade", "ciclos", "health"),
    "tela": ("polegada", "polegadas", "hz", "trincada", "quebrada"),
    "display": ("polegada", "polegadas", "hz"),
    "chip": ("dual", "duplo", "nano", "esim"),
    "carregador": ("acompanha", "brinde", "junto", "original", "na caixa", "incluso"),
    "fone": ("acompanha", "brinde", "junto", "original", "na caixa", "incluso"),
    "cabo": ("acompanha", "brinde", "junto", "original", "na caixa", "incluso"),
    "capa": ("acompanha", "brinde", "junto", "de brinde", "incluso"),
    "suporte": ("acompanha", "brinde", "junto", "incluso", "tecnico"),
    "vidro": ("acompanha", "brinde", "junto", "incluso"),
}


def is_hard_blacklisted(title: str) -> bool:
    """True when the title contains an unambiguous accessory or part term."""
    normalized = normalize_text(title)
    padded = f" {normalized} "
    return any(f" {normalize_text(term)} " in padded for term in HARD_TERMS)


def is_contextual_part(title: str) -> bool:
    """True when an ambiguous term appears with no exempting marker.

    The raw title is inspected for '%' because normalization drops punctuation.
    """
    normalized = normalize_text(title)
    padded = f" {normalized} "
    for term, markers in CONTEXTUAL_TERMS.items():
        if f" {term} " not in padded:
            continue
        exempt = False
        for marker in markers:
            exempt = "%" in title if marker == "%" else marker in normalized
            if exempt:
                break
        if not exempt:
            return True
    return False
