"""Провайдер цен autoeuro.ru, API v2.

Документация: https://api.autoeuro.ru/doc/v2
Базовый путь: https://api.autoeuro.ru/api/v2/json/<action>/
Ключ передаётся в HTTP-заголовке `key`. Ответ всегда содержит META и либо DATA, либо ERROR.

Особенности, подтверждённые практикой (см. сторонние клиенты): числовые поля могут приходить
строками ("1250.00", "null"), даты — строкой «ГГГГ-ММ-ДД ЧЧ:ММ» в местном времени выбранного
варианта получения; сдвиг от Москвы даёт time_shift_msk из /get_deliveries.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.pricing.base import (
    BrandMatch,
    Offer,
    PriceProviderError,
    SupplierOrderItem,
    SupplierOrderResult,
)

log = logging.getLogger(__name__)

MOSCOW_UTC_OFFSET_HOURS = 3
SUPPLIER_NAME = "Авто-Евро"


def _to_float(value: Any) -> float | None:
    if isinstance(value, bool):  # bool — подкласс int, но float("True") упадёт
        return float(value)
    if value is None or value == "" or value == "null":
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


def _to_bool(value: Any) -> bool:
    return bool(_to_int(value))


def _to_dt(value: Any, tz: timezone) -> datetime | None:
    """«2026-10-27 18:00» или «2026-10-27 18:00:00» в местном времени точки получения."""
    if not value or value == "null":
        return None
    text = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    log.warning("autoeuro: не распознан формат даты %r", value)
    return None


def normalize_currency(value: Any) -> str:
    """API отдаёт «руб.»; приводим к коду валюты."""
    text = str(value or "").strip().lower()
    if not text or "руб" in text or text in ("rub", "rur"):
        return "RUB"
    return str(value).strip().upper()


def supplier_label(is_stock: bool, is_dealer: bool, warehouse: str | None) -> str:
    """Человекочитаемый источник предложения: склад с названием или партнёр, плюс признак дилера."""
    if is_stock:
        label = f"Склад {SUPPLIER_NAME}" + (f" · {warehouse}" if warehouse else "")
    else:
        label = f"Партнёр {SUPPLIER_NAME}"
    return label + (" · официальный дилер" if is_dealer else "")


class AutoEuroProvider:
    name = "autoeuro"

    def __init__(
        self,
        api_key: str,
        delivery_key: str = "",
        base_url: str = "https://api.autoeuro.ru/api/v2/json",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        payer_key: str = "",
    ):
        if not api_key:
            raise PriceProviderError("AUTOEURO_API_KEY не задан")
        self._base_url = base_url.rstrip("/")
        self._delivery_key = delivery_key
        self._delivery_name: str | None = None
        self._delivery_resolved = False
        self._payer_key = payer_key
        self._payer_name: str | None = None
        self._payer_resolved = False
        self._tz = timezone(timedelta(hours=MOSCOW_UTC_OFFSET_HOURS))
        self._client = httpx.AsyncClient(
            headers={"key": api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._delivery_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    @property
    def delivery_key(self) -> str:
        return self._delivery_key

    @property
    def delivery_name(self) -> str | None:
        return self._delivery_name

    async def _call(
        self, action: str, params: dict[str, Any] | None = None, json_body: dict[str, Any] | None = None
    ) -> Any:
        """GET с параметрами в URL или POST с JSON-телом (для create_order)."""
        url = f"{self._base_url}/{action}/"
        try:
            if json_body is not None:
                response = await self._client.post(url, json=json_body)
            else:
                response = await self._client.get(url, params=params or {})
        except httpx.HTTPError as exc:
            raise PriceProviderError(f"autoeuro недоступен: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise PriceProviderError(
                f"autoeuro вернул не JSON (HTTP {response.status_code})"
            ) from exc

        if isinstance(payload, dict) and payload.get("ERROR"):
            error = payload["ERROR"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            code = error.get("code") if isinstance(error, dict) else None
            raise PriceProviderError(f"autoeuro: {message or 'ошибка'}", code=_to_int(code))
        if response.status_code >= 400:
            raise PriceProviderError(f"autoeuro: HTTP {response.status_code}")
        return payload.get("DATA") if isinstance(payload, dict) else payload

    async def get_deliveries(self) -> list[dict[str, Any]]:
        data = await self._call("get_deliveries")
        return [row for row in (data or []) if isinstance(row, dict)]

    async def get_balance(self) -> dict[str, Any]:
        data = await self._call("get_balance")
        if isinstance(data, list):
            return dict(data[0]) if data else {}
        return dict(data or {})

    async def get_payers(self) -> list[dict[str, Any]]:
        data = await self._call("get_payers")
        return [row for row in (data or []) if isinstance(row, dict)]

    @property
    def payer_name(self) -> str | None:
        return self._payer_name

    async def _resolve_payer_key(self) -> str:
        """Плательщик для заказов: из настроек или первый в /get_payers. Один раз на провайдер."""
        if self._payer_resolved:
            return self._payer_key
        async with self._delivery_lock:
            if self._payer_resolved:
                return self._payer_key
            payers = await self.get_payers()
            if not payers:
                raise PriceProviderError("autoeuro: у клиента нет плательщиков (/get_payers пуст)")
            chosen = next((p for p in payers if str(p.get("payer_key")) == self._payer_key), None)
            if chosen is None:
                if self._payer_key:
                    log.warning("autoeuro: AUTOEURO_PAYER_KEY не найден среди плательщиков, берём первого")
                chosen = payers[0]
            self._payer_key = str(chosen["payer_key"])
            self._payer_name = chosen.get("payer_name")
            self._payer_resolved = True
            log.info("autoeuro: плательщик «%s»", self._payer_name)
            return self._payer_key

    async def create_order(
        self, items: list[SupplierOrderItem], comment: str = "", wait_all: bool = True
    ) -> SupplierOrderResult:
        """POST /create_order/ с JSON-телом. Ответ: DATA=[{order_id, result, result_description}]."""
        if not items:
            raise PriceProviderError("autoeuro: нет позиций для заказа")
        delivery_key = await self._resolve_delivery_key()
        payer_key = await self._resolve_payer_key()
        body: dict[str, Any] = {
            "delivery_key": delivery_key,
            "payer_key": payer_key,
            "wait_all_goods": 1 if wait_all else 0,
            "stock_items": [
                {
                    "offer_key": i.offer_key,
                    "quantity": int(i.quantity),
                    **({"comment": i.comment[:255]} if i.comment else {}),
                }
                for i in items
            ],
        }
        if comment:
            body["comment"] = comment[:255]
        data = await self._call("create_order", json_body=body)
        row = data[0] if isinstance(data, list) and data else data
        if not isinstance(row, dict):
            raise PriceProviderError("autoeuro: неожиданный ответ create_order")
        order_id = row.get("order_id")
        accepted = _to_bool(row.get("result")) and order_id not in (None, "", "null", 0, "0")
        return SupplierOrderResult(
            accepted=accepted,
            order_id=str(order_id) if accepted else None,
            message=str(row.get("result_description") or ""),
        )

    def _apply_delivery(self, delivery: dict[str, Any]) -> None:
        self._delivery_key = str(delivery["delivery_key"])
        # В документации поле называется name, фактически API отдаёт delivery_name
        self._delivery_name = delivery.get("delivery_name") or delivery.get("name")
        shift = _to_float(delivery.get("time_shift_msk")) or 0.0
        self._tz = timezone(timedelta(hours=MOSCOW_UTC_OFFSET_HOURS + shift))
        self._delivery_resolved = True

    async def _resolve_delivery_key(self) -> str:
        """Выбрать вариант получения и его часовой пояс. Один раз на время жизни провайдера."""
        if self._delivery_resolved:
            return self._delivery_key
        async with self._delivery_lock:
            if self._delivery_resolved:
                return self._delivery_key
            deliveries = await self.get_deliveries()
            if not deliveries:
                raise PriceProviderError("autoeuro: у клиента нет вариантов получения товара")
            chosen = next(
                (d for d in deliveries if str(d.get("delivery_key")) == self._delivery_key),
                None,
            )
            if chosen is None:
                if self._delivery_key:
                    log.warning(
                        "autoeuro: AUTOEURO_DELIVERY_KEY=%s не найден среди вариантов, берём первый",
                        self._delivery_key,
                    )
                chosen = deliveries[0]
            self._apply_delivery(chosen)
            log.info("autoeuro: вариант получения «%s», часовой пояс %s", self._delivery_name, self._tz)
            return self._delivery_key

    async def search_brands(self, code: str) -> list[BrandMatch]:
        data = await self._call("search_brands", {"code": code})
        result: list[BrandMatch] = []
        for row in data or []:
            if not isinstance(row, dict) or not row.get("brand"):
                continue
            result.append(
                BrandMatch(brand=str(row["brand"]), code=str(row.get("code") or code), name=row.get("name"))
            )
        return result

    def parse_item(self, row: dict[str, Any], brand: str, code: str) -> Offer | None:
        price = _to_float(row.get("price"))
        if price is None:
            return None
        is_stock = _to_bool(row.get("stock"))
        is_dealer = _to_bool(row.get("dealer"))
        warehouse = row.get("warehouse_name") or None
        if warehouse == "null":
            warehouse = None
        return Offer(
            brand=str(row.get("brand") or brand),
            code=str(row.get("code") or code),
            name=row.get("name") or None,
            price=price,
            currency=normalize_currency(row.get("currency")),
            amount=_to_int(row.get("amount")) or 0,
            is_stock=is_stock,
            is_dealer=is_dealer,
            supplier=supplier_label(is_stock, is_dealer, warehouse),
            warehouse=warehouse,
            cross_type=_to_int(row.get("cross")),
            delivery_time=_to_dt(row.get("delivery_time"), self._tz),
            delivery_time_max=_to_dt(row.get("delivery_time_max"), self._tz),
            order_before=_to_dt(row.get("order_before"), self._tz),
            rejects=_to_float(row.get("rejects")),
            packing=_to_int(row.get("packing")) or 0,
            unit=row.get("unit") or None,
            offer_key=row.get("offer_key") or None,
            raw=row,
        )

    async def search_items(self, brand: str, code: str, with_crosses: bool = True) -> list[Offer]:
        delivery_key = await self._resolve_delivery_key()
        data = await self._call(
            "search_items",
            {
                "brand": brand,
                "code": code,
                "delivery_key": delivery_key,
                "with_crosses": 1 if with_crosses else 0,
                "with_offers": 1,
            },
        )
        offers: list[Offer] = []
        for row in data or []:
            if not isinstance(row, dict):
                continue
            offer = self.parse_item(row, brand, code)
            if offer is not None:
                offers.append(offer)
        return offers
