# -*- coding: utf-8 -*-
from typing import Dict, List, Tuple

# Словарь весов по умолчанию. 
# 1.0 = базовая стоимость прокладки, 999 = непреодолимое препятствие (вода)
DEFAULT_OSM_WEIGHTS = {
    "highway:motorway": 10.0,
    "highway:trunk": 8.0,
    "highway:primary": 5.0,
    "highway:secondary": 3.0,
    "highway:residential": 2.0,
    "landuse:forest": 4.0,
    "landuse:industrial": 6.0,
    "natural:water": 999.0,
    "natural:wood": 4.0,
    "waterway:river": 999.0,
    "default": 1.0 # Стоимость по умолчанию для незасеченных территорий
}

def get_default_weights_table() -> List[Dict[str, any]]:
    """Возвращает список словарей для инициализации таблицы весов в UI."""
    return [{"tag": tag, "weight": weight} for tag, weight in DEFAULT_OSM_WEIGHTS.items() if tag != "default"]

def parse_osm_tag(key: str, value: str) -> str:
    """Формирует строковый ключ для сопоставления, например 'highway:motorway'."""
    return f"{key}:{value}".lower()