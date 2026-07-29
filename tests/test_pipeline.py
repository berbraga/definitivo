from renov_market_scan.filtering.pipeline import FilterContext, run_pipeline
from renov_market_scan.models import Listing

CONTEXT = FilterContext(
    storage_gb=128,
    manufacturer="APPLE",
    model="IPHONE 13",
    include_new=False,
    price_floor_brl=80.0,
    price_ceiling_brl=15000.0,
)


def make_listing(**overrides) -> Listing:
    base = {
        "search_key": "k",
        "source": "olx",
        "title": "iPhone 13 128GB seminovo R$ 3.050,00",
        "price_brl": 3050.0,
        "condition": "seminovo",
        "url": "https://sp.olx.com.br/celulares/iphone-13-128gb-seminovo-1",
        "captured_at": "2026-07-28T10:00:00",
        "cited_text": "iPhone 13 128GB seminovo R$ 3.050,00",
    }
    base.update(overrides)
    return Listing(**base)


def reasons(rejected) -> list[str]:
    return [item.reason for item in rejected]


def test_a_clean_listing_is_accepted():
    accepted, rejected = run_pipeline([make_listing()], CONTEXT)
    assert len(accepted) == 1
    assert rejected == []
    assert accepted[0].price_brl == 3050.0
    assert accepted[0].condition == "seminovo"


def test_accessory_is_rejected_first():
    listing = make_listing(title="Capa capinha iPhone 13 128GB R$ 90,00")
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["acessorio_ou_peca"]


def test_pro_max_is_rejected_as_model_mismatch():
    listing = make_listing(
        title="iPhone 13 Pro Max 128GB Gold seminovo R$ 3.200,00",
        cited_text="iPhone 13 Pro Max 128GB Gold seminovo R$ 3.200,00",
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["modelo_divergente"]


def test_missing_capacity_is_rejected():
    listing = make_listing(
        title="iPhone 13 seminovo R$ 3.050,00",
        cited_text="iPhone 13 seminovo R$ 3.050,00",
        url="https://sp.olx.com.br/celulares/iphone-13-seminovo-1",
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["capacidade_ausente"]


def test_battery_health_survives_the_contextual_rule():
    text = "iPhone 13 128GB Branco Saude de bateria 90% R$ 3.050,00 | Loja Fisica |"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert len(accepted) == 1, reasons(rejected)


def test_a_battery_part_is_rejected_after_model_and_capacity():
    text = "Bateria original iPhone 13 128GB nova R$ 150,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["peca_provavel"]


def test_new_is_excluded_by_default():
    text = "iPhone 13 128GB lacrado na caixa R$ 4.200,00"
    accepted, rejected = run_pipeline(
        [make_listing(title=text, cited_text=text, condition="novo")], CONTEXT
    )
    assert accepted == []
    assert reasons(rejected) == ["condicao_excluida"]


def test_new_is_included_when_requested():
    text = "iPhone 13 128GB lacrado na caixa R$ 4.200,00"
    context = FilterContext(**{**CONTEXT.__dict__, "include_new": True})
    accepted, _ = run_pipeline([make_listing(title=text, cited_text=text)], context)
    assert len(accepted) == 1
    assert accepted[0].condition == "novo"


def test_unknown_condition_is_kept_because_most_titles_omit_it():
    """A title with no condition word must stay in the sample.

    The advert measured in Phase 0 — 'iPhone 13 128GB Branco Saude de bateria 90%
    R$ 3.050,00' — names no condition, and it is the norm rather than the
    exception. Excluding 'desconhecido' would discard most real listings and bias
    what survives toward sellers who happen to write 'usado'. Only 'novo' is
    excluded by default, because a sealed unit is a different market.
    """
    text = "iPhone 13 128GB Branco R$ 3.050,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert len(accepted) == 1, reasons(rejected)
    assert accepted[0].condition == "desconhecido"


def test_installment_only_is_rejected():
    text = "iPhone 13 128GB seminovo 12x R$ 254,17 sem juros"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_parcelado"]


def test_our_parser_wins_over_the_model_reported_price():
    text = "iPhone 13 128GB seminovo R$ 3.050,00 ou 12x R$ 254,17"
    listing = make_listing(title=text, cited_text=text, price_brl=254.17)
    accepted, _ = run_pipeline([listing], CONTEXT)
    assert accepted[0].price_brl == 3050.0


def test_price_without_evidence_is_rejected():
    listing = make_listing(
        title="iPhone 13 128GB seminovo",
        cited_text="iPhone 13 128GB seminovo, tratar",
        price_brl=3050.0,
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_sem_evidencia"]


def test_absent_price_everywhere_is_rejected():
    listing = make_listing(
        title="iPhone 13 128GB seminovo", cited_text="iPhone 13 128GB seminovo", price_brl=None
    )
    accepted, rejected = run_pipeline([listing], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_ausente"]


def test_price_below_the_floor_is_rejected():
    text = "iPhone 13 128GB seminovo R$ 50,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_fora_de_faixa"]


def test_price_above_the_ceiling_is_rejected():
    text = "iPhone 13 128GB seminovo R$ 99.000,00"
    accepted, rejected = run_pipeline([make_listing(title=text, cited_text=text)], CONTEXT)
    assert accepted == []
    assert reasons(rejected) == ["preco_fora_de_faixa"]


def test_duplicates_are_rejected_with_a_reason():
    first = make_listing()
    second = make_listing(url=make_listing().url + "?utm_source=x")
    accepted, rejected = run_pipeline([first, second], CONTEXT)
    assert len(accepted) == 1
    assert reasons(rejected) == ["duplicado"]


def test_five_g_flag_reaches_the_accepted_listing():
    context = FilterContext(
        **{**CONTEXT.__dict__, "manufacturer": "SAMSUNG", "model": "GALAXY A17"}
    )
    text = "Samsung Galaxy A17 5G 128GB usado R$ 900,00"
    accepted, _ = run_pipeline([make_listing(title=text, cited_text=text)], context)
    assert len(accepted) == 1
    assert accepted[0].flag_5g_divergent is True


def test_every_rejection_carries_a_reason():
    listings = [
        make_listing(title="Capa iPhone 13", cited_text="Capa iPhone 13"),
        make_listing(title="iPhone 13 Pro 128GB R$ 1,00", cited_text="iPhone 13 Pro 128GB R$ 1,00"),
    ]
    _, rejected = run_pipeline(listings, CONTEXT)
    assert len(rejected) == 2
    assert all(item.reason for item in rejected)
