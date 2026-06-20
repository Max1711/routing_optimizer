# -*- coding: utf-8 -*-
import pandas as pd
import geopandas as gpd
from typing import List, Tuple, Optional
from qgis.core import (
    QgsVectorLayer, QgsProject, QgsCoordinateReferenceSystem, 
    QgsCoordinateTransform, QgsPointXY, QgsFeature, QgsGeometry, 
    QgsMessageLog, Qgis
)
from PyQt5.QtCore import QVariant
from ..utils.crs_utils import is_metric_crs

class DataManager:
    """Управляет загрузкой, преобразованием и добавлением пространственных данных."""

    @staticmethod
    def load_tabular_data(file_path: str, file_type: str) -> pd.DataFrame:
        """Загружает данные из Excel или CSV."""
        try:
            if file_type == 'csv':
                df = pd.read_csv(file_path)
            else:
                df = pd.read_excel(file_path)
            return df
        except Exception as e:
            QgsMessageLog.logMessage(f"Ошибка загрузки файла: {e}", "RoutingOptimizer", Qgis.Critical)
            raise

    @staticmethod
    def create_memory_layer(
        points: List[Tuple[str, str, float, float, str]], 
        target_crs: QgsCoordinateReferenceSystem,
        layer_name: str = "Optimized_Points"
    ) -> Optional[QgsVectorLayer]:
        """
        Создает временный векторный слой из списка точек и добавляет его на карту.
        
        :param points: Список кортежей (ID, Type, X, Y, SourceCRS)
        :param target_crs: Целевая метрическая CRS
        :param layer_name: Имя слоя
        """
        if not points:
            return None

        # Определяем CRS для создания слоя (строка формата "EPSG:XXXX")
        crs_auth_id = target_crs.authid()
        uri = f"Point?crs={crs_auth_id}&field=id:string&field=type:string&field=x:double&field=y:double&field=source_crs:string"
        
        layer = QgsVectorLayer(uri, layer_name, "memory")
        if not layer.isValid():
            QgsMessageLog.logMessage("Не удалось создать временный слой", "RoutingOptimizer", Qgis.Critical)
            return None

        # Заполняем слой данными
        layer.startEditing()
        for pid, ptype, x, y, src_crs in points:
            feature = QgsFeature()
            feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            feature.setAttributes([pid, ptype, x, y, src_crs])
            layer.addFeature(feature)
            
        layer.commitChanges()
        
        # Добавляем на карту
        QgsProject.instance().addMapLayer(layer)
        return layer