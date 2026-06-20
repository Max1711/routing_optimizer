# -*- coding: utf-8 -*-
from qgis.core import QgsCoordinateReferenceSystem, QgsUnitTypes

def is_metric_crs(crs: QgsCoordinateReferenceSystem) -> bool:
    """
    Проверяет, является ли система координат метрической.
    
    :param crs: Проверяемая CRS
    :return: True, если единицы измерения в метрах
    """
    if not crs.isValid():
        return False
    return crs.mapUnits() == QgsUnitTypes.DistanceMeters