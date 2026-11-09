"""Правила категорий каталога: названия из реального прайса autoeuro → ожидаемая категория."""

import re
from pathlib import Path

import pytest

from app.services.pricelist import categories
from app.services.pricelist.categories import (
    CATEGORIES,
    CATEGORY_BY_SLUG,
    OTHER_SLUG,
    assign_category,
)

ICONS_TEMPLATE = Path(__file__).parent.parent / "app" / "templates" / "partials" / "icons.html"

# Названия взяты из прайса дословно: регистр, сокращения, опечатки и артикулы сохранены.
CASES = [
    ("Фильтр салона TOYOTA YARIS/YARIS VERSO 1.0/1.3/1.5/1.4D-4D 99>", "filters"),
    ("LAC-1227C Фильтр салонный угольный", "filters"),  # артикул перед названием
    ("ФИЛЬTP OЧИCTKИ MACЛA [ORG]", "filters"),  # латинские буквы-двойники
    ("Масло моторное ZIC X5 Diesel 5W-30 1л.", "oils"),
    ("Антифриз Febi фиолетовый -35c G12+ 1л.", "oils"),
    ("Жидкость тормозная Febi DOT4 Plus 1л.", "oils"),
    ("Диск торм.зад.PEUGEOT BOXER II,FIAT DUCATO Q18,CITROEN JUMPER II", "brakes"),
    ("Колодки тормозные CHEVROLET Malibu", "brakes"),
    ("Ремкомплект суппорта тормозного nissan patrol gr ii (y61) 01-97->", "brakes"),
    ("Датчик износа тормозных колодок MINI R50,R53,R55,R56,R57", "brakes"),
    ("Трос ручного тормоза Iveco Daily IV 06- (MT8425)", "brakes"),
    ("С-б.пер.рычага (зад.)HYUNDAI Solaris", "suspension"),
    ("Нак.рул.тяги лев.CITROEN/PEUGEOT/FIAT JUMPER/BOXER/DUCATO 2006 =>", "suspension"),
    ("Амортизатор зад.NISSAN QASHQAI J10E 06> (газ.)", "suspension"),
    ("Втулка стабилизатора CR-V (06-11)", "suspension"),
    ("Подшип. опоры амортизатора OPEL VECTRA C 02-08, ASTRA H 04-14", "suspension"),
    ("Комплект прокладок гбц opel x25xe", "engine"),  # слово-обёртка «комплект»
    ("Вкладыши шатунные CHEVROLET Lacetti STD (к-кт 8шт)", "engine"),
    ("Ремень ручейковый 6PK1875", "belts"),
    ("Свеча зажигания NISSAN/RENAULT/PEUGEOT", "electrics"),
    ("Датчик ABS задний левый MERCEDES-BENZ Sprinter VW Crafter (2E,2F)", "electrics"),
    ("Реле втягивающее стартера для а/м Ford Transit (06-) 2.2D", "electrics"),
    ("Термостат BMW 3 (E90) (05-) 1 (E81) (04-)", "cooling"),
    ("Электорвентилятор opel corsa d 07.06-", "cooling"),  # опечатка из прайса
    ("Компрессор кондиционера", "climate"),
    ("Бензонасос электрический", "fuel"),
    ("Гофра глушителя 45x200 3-х слойная", "exhaust"),
    ("Хомут глушителя [ORG]", "exhaust"),
    ("Кронштейн крепления глушителя NISSAN X-TRAIL 2007-2015", "exhaust"),  # узел в конце
    ("ШРУС HYUNDAI ELANTRA (MD, UD) 06.11- , 42887", "transmission"),
    (
        "К-кт пыльника ШРУСа внутр. MERCEDES C-CLASS (W204) 07-14/S-CLASS (W221) 05-13",
        "transmission",
    ),
    ("Сальник КПП NISSAN X-TRAIL (T30) 2.0 4x4 [2001/07-.]", "transmission"),
    ("Опора кардана подвесная HYUNDAI IX35 10>", "transmission"),
    ("ШАРИКОВЫЙ ПОДШИПНИК D=36ММ[ORG]", "transmission"),  # прилагательное впереди
    ("Щётки с/о бескаркасные к-кт SWF 630+550mm", "body"),
    ("Трос замка капота [ORG]", "body"),
    ("Коврики автомобильные влаговпитывающие 50*38см-2шт.+25*38см-2шт", "interior"),
    ("Болт AUDI/VW", "fasteners"),
    ("КЛИПСА", "fasteners"),
    ("Гайка колесная M12х1.5(R)х30, 5мм", "fasteners"),
    ("Хомут, двойной AUDI A3 2003-2012", "fasteners"),
    ("Шайба [ORG]", "fasteners"),
    ("Набор фиксаторов валов Volvo 850,960,S40,S70,S90 . VERTUL", "tools"),
    ("Ключ комбинированный 32мм ARNEZI R1030032", "tools"),
    ("LAVR Очиститель кондиционера MINT, 210 мл", "tools"),  # бренд перед названием
    ("Крышка AUDI/VW", OTHER_SLUG),  # общее слово без указания узла
]

# Одно и то же первое слово, разные категории: побеждает более специфичное правило.
PRIORITY_GROUPS = [
    [
        ("Диск тормозной задний для а/м Kia Sorento II (09-) d=302", "brakes"),
        ("Диск сцепления Ford Focus 1.6i1.8i 16V 98>", "transmission"),
    ],
    [
        ("Насос топливный AUDI/VW 1.2-2.0 08-> (3bar)", "fuel"),
        ("Насос водяной AUDI/VW 1.8/2.0TFSI 2012 (без ремня)", "cooling"),
        ("Насос омывателя стекла", "body"),
        ("Насос гидроусилителя руля", "suspension"),
    ],
    [
        ("Щетка стеклоочистителя задняя каркасная 350 мм 11 адаптеров SM-WBS350R", "body"),
        ("Щетка от снега и льда с мягкой щетиной и двойным скребком (80 см)", "tools"),
        ("Щетка мотора отопителя (1шт.)", "climate"),
    ],
    [
        ("Прокладка ГБЦ DAEWOO Nexia (1.5) (SOHC)", "engine"),
        ("Прокладка глушителя", "exhaust"),
    ],
    [
        ("Радиатор охлаждения паяный", "cooling"),
        ("Радиатор печки", "climate"),
    ],
    [
        ("Головка блока цилиндров", "engine"),
        ("Головка торцевая 1/4 DR шестигранная 8мм (AT-S14-04)", "tools"),
    ],
    [
        ("Натяжитель цепи BMW", "engine"),
        ("Натяжитель руч.ремня с роликом CITROEN/PEUGEOT/MINI 1.4/1.6 2006 =>", "belts"),
    ],
    [
        ("Форсунка бензиновая", "fuel"),
        ("Форсунка омывателя [ORG]", "body"),
    ],
]


@pytest.mark.parametrize(("name", "slug"), CASES)
def test_assign_category(name, slug):
    assert assign_category(name) == slug


@pytest.mark.parametrize("group", PRIORITY_GROUPS, ids=lambda g: g[0][0].split()[0])
def test_specific_rule_wins(group):
    assert [assign_category(name) for name, _ in group] == [slug for _, slug in group]


@pytest.mark.parametrize(
    "name",
    ["Колодки тормозные CHEVROLET Malibu", "Фильтр салонный HYUNDAI Solaris 10-", "Болт AUDI/VW"],
)
def test_case_does_not_matter(name):
    assert assign_category(name.upper()) == assign_category(name.lower()) == assign_category(name)


@pytest.mark.parametrize("name", ["", "   ", "[ORG]", "12345", "AUDI/VW"])
def test_empty_or_meaningless_name_is_other(name):
    assert assign_category(name) == OTHER_SLUG


def test_slugs_unique_and_index_consistent():
    slugs = [c.slug for c in CATEGORIES]
    assert len(slugs) == len(set(slugs))
    assert list(CATEGORY_BY_SLUG) == slugs
    assert all(CATEGORY_BY_SLUG[c.slug] is c for c in CATEGORIES)


def test_categories_shape():
    assert 14 <= len(CATEGORIES) <= 20
    assert CATEGORIES[-1].slug == OTHER_SLUG
    assert CATEGORY_BY_SLUG[OTHER_SLUG].patterns == ()
    for c in CATEGORIES:
        assert re.fullmatch(r"[a-z]+", c.slug)
        assert re.search(r"[А-Яа-яЁё]", c.title)
        for pattern in c.patterns:
            re.compile(pattern)


def test_icons_exist_in_template():
    known = set(re.findall(r"name == '([a-z]+)'", ICONS_TEMPLATE.read_text(encoding="utf-8")))
    assert {c.icon for c in CATEGORIES} <= known | {"box"}


def test_every_category_takes_part_in_matching():
    # Категория, забытая в порядке проверки, никогда не была бы назначена.
    expected = sorted(c.slug for c in CATEGORIES if c.slug != OTHER_SLUG)
    assert sorted(categories._MATCH_ORDER) == expected


def test_result_is_known_slug():
    for name, _ in CASES:
        assert assign_category(name) in CATEGORY_BY_SLUG
