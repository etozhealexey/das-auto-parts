"""group_offers: искомая деталь отдельно, аналоги по бренд+артикул."""

from app.services.pricing.grouping import OfferGroup, SearchResult, group_offers, sort_offers
from tests.helpers import dt, make_offer


def test_empty_input():
    result = group_offers([])
    assert isinstance(result, SearchResult)
    assert result.originals == []
    assert result.analog_groups == []
    assert result.total == 0
    assert result.analogs_count == 0


def test_sort_offers_stock_first_then_price():
    a = make_offer(price=300.0, is_stock=False)
    b = make_offer(price=500.0, is_stock=True)
    c = make_offer(price=200.0, is_stock=True)
    d = make_offer(price=100.0, is_stock=False)
    assert sort_offers([a, b, c, d]) == [c, b, d, a]


def test_originals_are_sorted_by_stock_then_price_and_exclude_analogs():
    partner_cheap = make_offer(cross_type=None, price=2037.38, is_stock=False, offer_key="p1")
    stock_expensive = make_offer(cross_type=None, price=2600.0, is_stock=True, offer_key="s2")
    stock_cheap = make_offer(cross_type=None, price=2500.0, is_stock=True, offer_key="s1")
    analog = make_offer(brand="NISSHINBO", code="PF-1521", cross_type=0, price=100.0, is_stock=True)

    result = group_offers([partner_cheap, analog, stock_expensive, stock_cheap])

    assert result.originals == [stock_cheap, stock_expensive, partner_cheap]
    assert result.total == 4
    assert result.analogs_count == 1


def test_analogs_grouped_by_brand_and_code_case_insensitively():
    offers = [
        make_offer(brand="Nisshinbo", code="pf-1521", cross_type=0, price=9250.05, offer_key="n1"),
        make_offer(brand="NISSHINBO", code="PF-1521", cross_type=0, price=9100.0, offer_key="n2"),
        make_offer(brand=" nisshinbo ", code=" PF-1521 ", cross_type=3, price=9000.0, offer_key="n3"),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, price=1395.47, offer_key="s1"),
    ]
    result = group_offers(offers)

    assert len(result.analog_groups) == 2
    nisshinbo = next(g for g in result.analog_groups if g.brand.strip().upper() == "NISSHINBO")
    assert nisshinbo.count == 3
    assert [o.offer_key for o in nisshinbo.offers] == ["n3", "n2", "n1"]  # внутри группы по цене
    # бренд и артикул берутся из первого встреченного предложения
    assert (nisshinbo.brand, nisshinbo.code) == ("Nisshinbo", "pf-1521")


def test_group_name_taken_from_first_nonempty():
    offers = [
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, name=None, price=10.0),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, name="", price=11.0),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, name="Колодки TOYOTA CAMRY 06=>", price=12.0),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, name="Другое имя", price=13.0),
    ]
    (group,) = group_offers(offers).analog_groups
    assert group.name == "Колодки TOYOTA CAMRY 06=>"


def test_group_aggregates():
    offers = [
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, price=1500.0, is_stock=False, amount=3,
                   delivery_time=dt(5, month=11)),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, price=1395.47, is_stock=True, amount=1,
                   delivery_time=None),
        make_offer(brand="SANGSIN", code="SP2080", cross_type=3, price=1600.0, is_stock=False, amount=10,
                   delivery_time=dt(31)),
    ]
    (group,) = group_offers(offers).analog_groups
    assert group.count == 3
    assert group.min_price == 1395.47
    assert group.in_stock is True
    assert group.total_amount == 14
    assert group.best_delivery == dt(31)
    assert [o.price for o in group.offers] == [1395.47, 1500.0, 1600.0]


def test_group_without_dates_has_no_best_delivery_and_not_in_stock():
    offers = [make_offer(brand="A", code="1", cross_type=0, price=5.0, is_stock=False, delivery_time=None)]
    (group,) = group_offers(offers).analog_groups
    assert group.best_delivery is None
    assert group.in_stock is False
    assert group.min_price == 5.0


def test_groups_ordered_in_stock_first_then_by_min_price():
    offers = [
        make_offer(brand="EXPENSIVE-STOCK", code="1", cross_type=0, price=5000.0, is_stock=True),
        make_offer(brand="CHEAP-ORDER", code="2", cross_type=0, price=100.0, is_stock=False),
        make_offer(brand="CHEAP-STOCK", code="3", cross_type=3, price=900.0, is_stock=False),
        make_offer(brand="CHEAP-STOCK", code="3", cross_type=3, price=1000.0, is_stock=True),
        make_offer(brand="MID-ORDER", code="4", cross_type=0, price=500.0, is_stock=False),
    ]
    result = group_offers(offers)
    assert [g.brand for g in result.analog_groups] == ["CHEAP-STOCK", "EXPENSIVE-STOCK", "CHEAP-ORDER", "MID-ORDER"]
    assert result.analog_groups[0].min_price == 900.0


def test_same_brand_different_codes_are_different_groups():
    offers = [
        make_offer(brand="BREMBO", code="P83117", cross_type=0, price=10.0),
        make_offer(brand="BREMBO", code="P83118", cross_type=0, price=20.0),
    ]
    assert [(g.brand, g.code) for g in group_offers(offers).analog_groups] == [("BREMBO", "P83117"), ("BREMBO", "P83118")]


def test_original_with_same_brand_code_as_analog_stays_in_originals():
    """cross_type решает, а не совпадение бренда и артикула с запросом."""
    original = make_offer(brand="TOYOTA", code="0446533471", cross_type=None, price=2037.38)
    synonym = make_offer(brand="TOYOTA", code="0446533471", cross_type=2, price=1900.0)
    result = group_offers([synonym, original])
    assert result.originals == [original]
    assert len(result.analog_groups) == 1 and result.analog_groups[0].offers == [synonym]


def test_offer_group_count_property():
    g = OfferGroup(brand="A", code="1", name=None, offers=[make_offer(price=1.0), make_offer(price=2.0)])
    assert g.count == 2 and g.min_price == 1.0
