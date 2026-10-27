"""Группировка предложений для страницы поиска: искомая деталь отдельно, аналоги по бренду+артикулу."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.services.pricing.base import Offer

_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


def sort_offers(offers: list[Offer]) -> list[Offer]:
    """Сначала наличие на складе, затем по цене, при равной цене — по сроку (без срока в конец)."""
    return sorted(offers, key=lambda o: (not o.is_stock, o.price, o.delivery_time or _FAR_FUTURE))


@dataclass
class OfferGroup:
    brand: str
    code: str
    name: str | None
    offers: list[Offer] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.offers)

    @property
    def min_price(self) -> float:
        return min(o.price for o in self.offers)

    @property
    def in_stock(self) -> bool:
        return any(o.is_stock for o in self.offers)

    @property
    def total_amount(self) -> int:
        return sum(o.amount for o in self.offers)

    @property
    def best_delivery(self) -> datetime | None:
        dates = [o.delivery_time for o in self.offers if o.delivery_time]
        return min(dates) if dates else None


@dataclass
class SearchResult:
    originals: list[Offer]  # предложения искомой детали, отсортированы
    analog_groups: list[OfferGroup]  # аналоги, сгруппированы по бренд+артикул

    @property
    def total(self) -> int:
        return len(self.originals) + sum(g.count for g in self.analog_groups)

    @property
    def analogs_count(self) -> int:
        return sum(g.count for g in self.analog_groups)


def group_offers(offers: list[Offer]) -> SearchResult:
    originals = sort_offers([o for o in offers if o.cross_type is None])
    groups: dict[tuple[str, str], OfferGroup] = {}
    for o in offers:
        if o.cross_type is None:
            continue
        key = (o.brand.strip().upper(), o.code.strip().upper())
        group = groups.get(key)
        if group is None:
            group = groups[key] = OfferGroup(brand=o.brand, code=o.code, name=o.name)
        elif not group.name and o.name:
            group.name = o.name
        group.offers.append(o)
    for g in groups.values():
        g.offers = sort_offers(g.offers)
    analog_groups = sorted(groups.values(), key=lambda g: (not g.in_stock, g.min_price))
    return SearchResult(originals=originals, analog_groups=analog_groups)
