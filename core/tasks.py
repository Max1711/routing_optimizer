# -*- coding: utf-8 -*-
import os
import tempfile
import traceback
import time
import shutil
import numpy as np
import pandas as pd
from typing import Dict, Optional, List, Tuple

from qgis.core import (
    QgsTask, QgsMessageLog, Qgis, QgsCoordinateReferenceSystem, 
    QgsRasterLayer, QgsProject, QgsVectorLayer, QgsFeature, 
    QgsGeometry, QgsPointXY, QgsLineSymbol, QgsSingleSymbolRenderer,
    QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
)
from qgis.PyQt.QtGui import QColor

from osgeo import gdal, ogr, osr
import geopandas as gpd
from shapely.geometry import box

try:
    from pyrosm import OSM
    HAS_PYROSM = True
except ImportError:
    HAS_PYROSM = False


class CostSurfaceTask(QgsTask):
    def __init__(self, points_gdf: gpd.GeoDataFrame, pbf_path: str, buffer_meters: float, 
                 weights_dict: Dict[str, float], target_crs: QgsCoordinateReferenceSystem, 
                 resolution_m: float = 10.0, dem_path: Optional[str] = None, elev_weight: float = 0.0):
        super().__init__("Построение Cost-поверхности", QgsTask.CanCancel)
        self.points_gdf = points_gdf
        self.pbf_path = pbf_path
        self.buffer_meters = buffer_meters
        self.weights_dict = weights_dict
        self.target_crs = target_crs
        self.resolution_m = resolution_m
        self.dem_path = dem_path
        self.elev_weight = elev_weight
        self.result_raster_path: Optional[str] = None
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            QgsMessageLog.logMessage("TASK: Запуск задачи...", "RoutingOptimizer", Qgis.Info)
            if not HAS_PYROSM:
                self.exception = ImportError("pyrosm not installed")
                return False

            self.setProgress(5)
            if self.isCanceled(): return False

            # 1. Расчет Bounding Box
            QgsMessageLog.logMessage("TASK: Шаг 1/6 - Расчет Bounding Box (EPSG:4326)...", "RoutingOptimizer", Qgis.Info)
            gdf_4326 = self.points_gdf.to_crs("EPSG:4326")
            buffer_degrees = self.buffer_meters / 111000.0 
            
            minx, miny, maxx, maxy = gdf_4326.total_bounds
            minx -= buffer_degrees; miny -= buffer_degrees
            maxx += buffer_degrees; maxy += buffer_degrees
            
            bbox_list = [float(minx), float(miny), float(maxx), float(maxy)]
            
            self.setProgress(10)
            if self.isCanceled(): return False

            # 2. Чтение OSM
            QgsMessageLog.logMessage("TASK: Шаг 2/6 - Чтение и фильтрация OSM...", "RoutingOptimizer", Qgis.Info)
            osm = OSM(self.pbf_path, bounding_box=bbox_list)
            all_features: List[gpd.GeoDataFrame] = []

            for filter_key in ["highway", "landuse", "natural", "waterway"]:
                if self.isCanceled(): return False
                data = osm.get_data_by_custom_criteria(custom_filter={filter_key: True})
                if data is not None and not data.empty:
                    all_features.append(data)
            
            self.setProgress(40)
            if self.isCanceled(): return False

            if not all_features:
                self.exception = ValueError("Не найдено ни одного объекта OSM в заданном радиусе.")
                return False

            # 3. Подготовка векторных данных
            QgsMessageLog.logMessage("TASK: Шаг 3/6 - Конвертация и расчет весов...", "RoutingOptimizer", Qgis.Info)
            combined_gdf = pd.concat(all_features, ignore_index=True)
            combined_gdf.set_crs("EPSG:4326", inplace=True)
            target_crs_str = self.target_crs.authid()
            combined_gdf = combined_gdf.to_crs(target_crs_str)
            combined_gdf = combined_gdf[combined_gdf.is_valid & ~combined_gdf.is_empty]

            weights = []
            for _, row in combined_gdf.iterrows():
                tag_found = False
                for col in ['highway', 'landuse', 'natural', 'waterway']:
                    if col in row and pd.notna(row[col]):
                        val = row[col]
                        if isinstance(val, list): val = val[0]
                        tag_key = f"{col}:{str(val).lower()}"
                        if tag_key in self.weights_dict:
                            weights.append(self.weights_dict[tag_key])
                            tag_found = True
                            break
                if not tag_found:
                    weights.append(self.weights_dict.get("default", 1.0))
            combined_gdf['weight'] = weights

            self.setProgress(60)
            if self.isCanceled(): return False

            # 4. Создание базового OSM растра
            QgsMessageLog.logMessage("TASK: Шаг 4/6 - Растеризация OSM...", "RoutingOptimizer", Qgis.Info)
            temp_dir = tempfile.gettempdir()
            osm_raster_path = os.path.join(temp_dir, f"osm_base_{os.getpid()}.tif")
            self.result_raster_path = os.path.join(temp_dir, f"cost_surface_final_{os.getpid()}.tif")

            bounds = combined_gdf.total_bounds
            x_min, y_min, x_max, y_max = bounds
            x_res = max(10, int(np.ceil((x_max - x_min) / self.resolution_m)))
            y_res = max(10, int(np.ceil((y_max - y_min) / self.resolution_m)))

            driver = gdal.GetDriverByName('GTiff')
            dst_ds = driver.Create(osm_raster_path, x_res, y_res, 1, gdal.GDT_Float32, options=['COMPRESS=LZW', 'TILED=YES'])
            dst_ds.SetGeoTransform((x_min, self.resolution_m, 0, y_max, 0, -self.resolution_m))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(int(self.target_crs.authid().split(':')[1]))
            dst_ds.SetProjection(srs.ExportToWkt())
            
            band = dst_ds.GetRasterBand(1)
            nodata_val = 9999.0
            band.SetNoDataValue(nodata_val)
            band.Fill(self.weights_dict.get("default", 1.0))

            ogr_ds = ogr.GetDriverByName('Memory').CreateDataSource('memData')
            ogr_layer = ogr_ds.CreateLayer('weights', srs, ogr.wkbMultiPolygon)
            ogr_layer.CreateField(ogr.FieldDefn('weight', ogr.OFTReal))

            for _, row in combined_gdf.iterrows():
                if self.isCanceled(): 
                    dst_ds = None; return False
                if row.geometry.is_empty: continue
                feat = ogr.Feature(ogr_layer.GetLayerDefn())
                feat.SetGeometry(ogr.CreateGeometryFromWkb(row.geometry.wkb))
                feat.SetField('weight', float(row['weight']))
                ogr_layer.CreateFeature(feat)

            gdal.RasterizeLayer(dst_ds, [1], ogr_layer, options=["ATTRIBUTE=weight", "ALL_TOUCHED=TRUE"])
            dst_ds = None  
            ogr_ds = None  

            self.setProgress(75)
            if self.isCanceled(): return False

            # 5. Интеграция ЦМР (Рельефа), если предоставлена
            if self.dem_path and os.path.exists(self.dem_path) and self.elev_weight > 0:
                QgsMessageLog.logMessage("TASK: Шаг 5/6 - Интеграция ЦМР и расчет уклона...", "RoutingOptimizer", Qgis.Info)
                
                warped_dem_path = os.path.join(temp_dir, f"dem_warped_{os.getpid()}.tif")
                gdal.Warp(warped_dem_path, self.dem_path, format='GTiff', 
                          outputBounds=(x_min, y_min, x_max, y_max), 
                          width=x_res, height=y_res, resampleAlg=gdal.GRA_Bilinear)
                
                slope_path = os.path.join(temp_dir, f"slope_{os.getpid()}.tif")
                gdal.DEMProcessing(slope_path, warped_dem_path, "slope", format='GTiff', computeEdges=True)
                
                ds_osm = gdal.Open(osm_raster_path)
                ds_slope = gdal.Open(slope_path)
                
                arr_osm = ds_osm.GetRasterBand(1).ReadAsArray()
                arr_slope = ds_slope.GetRasterBand(1).ReadAsArray()
                slope_nodata = ds_slope.GetRasterBand(1).GetNoDataValue()
                
                # КРИТИЧЕСКИ ВАЖНО: Закрываем датасеты ДО удаления файлов
                ds_osm = None  
                ds_slope = None
                
                arr_slope = np.where(arr_slope == slope_nodata, 0.0, arr_slope)
                arr_slope = np.nan_to_num(arr_slope, nan=0.0)
                
                arr_final = arr_osm + (arr_slope * float(self.elev_weight))
                
                # Создаем финальный файл
                final_ds = driver.Create(self.result_raster_path, x_res, y_res, 1, gdal.GDT_Float32, options=['COMPRESS=LZW', 'TILED=YES'])
                if final_ds is None:
                    raise RuntimeError(f"GDAL не смог создать файл: {self.result_raster_path}")
                    
                final_ds.SetGeoTransform((x_min, self.resolution_m, 0, y_max, 0, -self.resolution_m))
                final_ds.SetProjection(srs.ExportToWkt())
                
                final_band = final_ds.GetRasterBand(1)
                final_band.SetNoDataValue(nodata_val)
                final_band.WriteArray(arr_final)
                final_band.FlushCache()
                final_ds = None # Освобождаем файл
                
                # Очистка временных файлов с небольшой задержкой для Windows
                time.sleep(0.2) 
                for f in [osm_raster_path, warped_dem_path, slope_path]:
                    try:
                        if os.path.exists(f):
                            os.remove(f)
                    except Exception:
                        pass

            else:
                time.sleep(0.2)
                try:
                    shutil.move(osm_raster_path, self.result_raster_path)
                except Exception:
                    shutil.copy2(osm_raster_path, self.result_raster_path)

            self.setProgress(100)
            QgsMessageLog.logMessage("TASK: Успешно завершено!", "RoutingOptimizer", Qgis.Success)
            return True

        except Exception as e:
            self.exception = e
            QgsMessageLog.logMessage(f"TASK: Критическая ошибка в run():\n{traceback.format_exc()}", "RoutingOptimizer", Qgis.Critical)
            return False

    def finished(self, result: bool):
        QgsMessageLog.logMessage(f"DEBUG finished: result={result}, path={self.result_raster_path}", "RoutingOptimizer", Qgis.Info)
        
        if result and self.result_raster_path:
            if not os.path.exists(self.result_raster_path):
                QgsMessageLog.logMessage(f"ОШИБКА: Файл растра не найден по пути: {self.result_raster_path}", "RoutingOptimizer", Qgis.Critical)
                return

            try:
                QgsMessageLog.logMessage("Попытка загрузки слоя в QGIS...", "RoutingOptimizer", Qgis.Info)
                layer = QgsRasterLayer(self.result_raster_path, "Cost_Surface")
                
                if layer.isValid():
                    # Теперь все классы корректно импортированы в начале файла
                    fcn = QgsColorRampShader()
                    fcn.setColorRampType(QgsColorRampShader.Interpolated)
                    lst = [
                        QgsColorRampShader.ColorRampItem(0, QColor('#ffffcc'), 'Низкая стоимость'),
                        QgsColorRampShader.ColorRampItem(5, QColor('#fd8d3c'), 'Средняя стоимость'),
                        QgsColorRampShader.ColorRampItem(20, QColor('#bd0026'), 'Высокая стоимость')
                    ]
                    fcn.setColorRampItemList(lst)
                    shader = QgsRasterShader()
                    shader.setRasterShaderFunction(fcn)
                    renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), 1, shader)
                    layer.setRenderer(renderer)
                    layer.triggerRepaint()
                    
                    QgsProject.instance().addMapLayer(layer)
                    QgsMessageLog.logMessage("✅ Cost-поверхность успешно добавлена на карту!", "RoutingOptimizer", Qgis.Success)
                else:
                    QgsMessageLog.logMessage(f"❌ Слой невалиден. Проверьте файл: {self.result_raster_path}", "RoutingOptimizer", Qgis.Critical)
            except Exception as e:
                QgsMessageLog.logMessage(f"❌ Исключение при добавлении слоя: {e}\n{traceback.format_exc()}", "RoutingOptimizer", Qgis.Critical)
        else:
            err_msg = str(self.exception) if self.exception else "Задача была отменена."
            QgsMessageLog.logMessage(f"Сбой задачи CostSurface: {err_msg}", "RoutingOptimizer", Qgis.Critical)

class AStarRoutingTask(QgsTask):
    def __init__(self, raster_path: str, start_points: List[Tuple[float, float]], 
                 end_points: List[Tuple[float, float]], target_crs: QgsCoordinateReferenceSystem):
        super().__init__("Поиск трассы (A* Сеть)", QgsTask.CanCancel)
        self.raster_path = raster_path
        self.start_points = start_points
        self.end_points = end_points
        self.target_crs = target_crs
        self.result_segments: List[List[Tuple[float, float]]] = []
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            QgsMessageLog.logMessage("ASTAR_TASK: Запуск построения сети...", "RoutingOptimizer", Qgis.Info)
            from ..algorithms.astar import RasterAStar
            
            self.setProgress(5)
            if self.isCanceled(): return False

            astar = RasterAStar(self.raster_path)
            
            # Итеративное построение сети (Greedy MST approach)
            connected_points = list(self.end_points) # Начинаем с ДНС/КНС
            unconnected_wells = list(self.start_points) # Все скважины изначально не подключены
            
            total_wells = len(unconnected_wells)
            wells_connected_count = 0

            while unconnected_wells and not self.isCanceled():
                best_segment = None
                min_cost = float('inf')
                well_to_connect = None

                # Ищем самую дешевую связь для каждой неподключенной скважины
                for well in unconnected_wells:
                    if self.isCanceled(): 
                        astar.close()
                        return False
                        
                    # Ищем путь от текущей скважины до ЛЮБОЙ уже подключенной точки
                    path, cost = astar.find_path(
                        start_coords=[well], 
                        end_coords=connected_points,
                        progress_callback=None # Отключаем внутренний прогресс, чтобы не дергать UI слишком часто
                    )
                    
                    if path and cost < min_cost:
                        min_cost = cost
                        best_segment = path
                        well_to_connect = well

                if best_segment and well_to_connect:
                    self.result_segments.append(best_segment)
                    connected_points.append(well_to_connect) # Теперь эта скважина часть сети
                    unconnected_wells.remove(well_to_connect)
                    wells_connected_count += 1
                    
                    # Обновляем прогресс: 5% + (90% * доля подключенных скважин)
                    progress = 5 + int((wells_connected_count / total_wells) * 90)
                    self.setProgress(progress)
                    QgsMessageLog.logMessage(f"ASTAR_TASK: Подключена скважина. Осталось: {len(unconnected_wells)}", "RoutingOptimizer", Qgis.Info)
                else:
                    QgsMessageLog.logMessage("ASTAR_TASK: Не удалось найти путь для оставшихся скважин (возможно, изолированы препятствиями).", "RoutingOptimizer", Qgis.Warning)
                    break

            astar.close()

            if self.isCanceled(): return False
            
            if not self.result_segments:
                self.exception = ValueError("Не удалось построить ни одного сегмента трассы.")
                return False

            self.setProgress(100)
            QgsMessageLog.logMessage(f"ASTAR_TASK: Сеть успешно построена! Сегментов: {len(self.result_segments)}", "RoutingOptimizer", Qgis.Success)
            return True

        except Exception as e:
            self.exception = e
            tb_str = traceback.format_exc()
            QgsMessageLog.logMessage(f"ASTAR_TASK: Критическая ошибка:\n{tb_str}", "RoutingOptimizer", Qgis.Critical)
            return False

    def finished(self, result: bool):
        if result and self.result_segments:
            crs_str = self.target_crs.authid()
            uri = f"LineString?crs={crs_str}&field=segment_id:string&field=length_m:double"
            layer = QgsVectorLayer(uri, "Optimized_Route_Network", "memory")
            
            if layer.isValid():
                layer.startEditing()
                
                total_length = 0.0
                for i, segment_coords in enumerate(self.result_segments):
                    line_geom = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in segment_coords])
                    seg_length = line_geom.length()
                    total_length += seg_length
                    
                    feature = QgsFeature()
                    feature.setGeometry(line_geom)
                    feature.setAttributes([f"Segment_{i+1}", seg_length])
                    layer.addFeature(feature)
                
                layer.commitChanges()
                
                # Стилизация: красная линия, толщиной 2
                symbol = QgsLineSymbol.createSimple({
                    'color': '#e31a1c',
                    'width': '2.5',
                    'penstyle': 'solid',
                    'linecap': 'round',
                    'linejoin': 'round'
                })
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
                
                QgsProject.instance().addMapLayer(layer)
                QgsMessageLog.logMessage(f"Маршрут добавлен на карту. Общая длина: {total_length:.2f} м", "RoutingOptimizer", Qgis.Success)
            else:
                QgsMessageLog.logMessage("Не удалось создать векторный слой маршрута.", "RoutingOptimizer", Qgis.Critical)
        else:
            err_msg = str(self.exception) if self.exception else "Задача была отменена."
            QgsMessageLog.logMessage(f"Сбой задачи AStarRouting: {err_msg}", "RoutingOptimizer", Qgis.Critical)

class GARoutingTask(QgsTask):
    """
    Асинхронная задача для оптимизации сети Генетическим Алгоритмом.
    """
    def __init__(self, raster_path: str, nodes: List[Tuple[str, float, float]], 
                 dns_indices: List[int], target_crs: QgsCoordinateReferenceSystem,
                 pop_size: int = 50, generations: int = 20, mutation_rate: float = 0.2, crossover_rate: float = 0.8):
        super().__init__("Оптимизация сети (GA)", QgsTask.CanCancel)
        self.raster_path = raster_path
        self.nodes = nodes
        self.dns_indices = dns_indices
        self.target_crs = target_crs
        self.pop_size = pop_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        
        self.best_chromosome: Optional[List[Tuple[int, int]]] = None
        self.best_fitness = float('inf')
        self.exception: Optional[Exception] = None

    def run(self) -> bool:
        try:
            QgsMessageLog.logMessage("GA_TASK: Запуск генетической оптимизации...", "RoutingOptimizer", Qgis.Info)
            from ..algorithms.genetic import NetworkGA
            from ..algorithms.astar import RasterAStar
            
            self.setProgress(5)
            if self.isCanceled(): return False

            astar = RasterAStar(self.raster_path)
            
            ga = NetworkGA(
                nodes=self.nodes,
                dns_indices=self.dns_indices,
                astar_router=astar,
                pop_size=self.pop_size,
                generations=self.generations,
                mutation_rate=self.mutation_rate,
                crossover_rate=self.crossover_rate,
                progress_callback=lambda p: self.setProgress(p) if not self.isCanceled() else None
            )
            
            self.best_chromosome, self.best_fitness = ga.run()
            astar.close()

            if self.isCanceled(): return False
            
            if not self.best_chromosome or self.best_fitness == float('inf'):
                self.exception = ValueError("ГА не смог найти валидное связное дерево.")
                return False

            self.setProgress(100)
            QgsMessageLog.logMessage(f"GA_TASK: Оптимизация завершена. Лучшая стоимость: {self.best_fitness:.2f}", "RoutingOptimizer", Qgis.Success)
            return True

        except Exception as e:
            self.exception = e
            tb_str = traceback.format_exc()
            QgsMessageLog.logMessage(f"GA_TASK: Критическая ошибка:\n{tb_str}", "RoutingOptimizer", Qgis.Critical)
            return False

    def finished(self, result: bool):
        if result and self.best_chromosome:
            crs_str = self.target_crs.authid()
            uri = f"LineString?crs={crs_str}&field=segment_id:string&field=length_m:double&field=cost:double"
            layer = QgsVectorLayer(uri, "Optimized_Route_GA", "memory")
            
            if layer.isValid():
                layer.startEditing()
                
                total_length = 0.0
                total_cost = 0.0
                
                for i, (idx1, idx2) in enumerate(self.best_chromosome):
                    node1 = self.nodes[idx1]
                    node2 = self.nodes[idx2]
                    
                    line_geom = QgsGeometry.fromPolylineXY([QgsPointXY(node1[1], node1[2]), QgsPointXY(node2[1], node2[2])])
                    # Для точной длины и стоимости нужно было бы сохранить пути из кэша ГА, 
                    # но для визуализации достаточно прямой линии между узлами (или можно доработать кэш)
                    # Здесь мы рисуем прямые сегменты топологии для наглядности структуры дерева.
                    
                    seg_length = line_geom.length()
                    total_length += seg_length
                    
                    feature = QgsFeature()
                    feature.setGeometry(line_geom)
                    feature.setAttributes([f"GA_Segment_{i+1}", seg_length, 0.0]) # Cost заглушка, т.к. кэш внутри GA
                    layer.addFeature(feature)
                
                layer.commitChanges()
                
                # Стилизация: синяя линия (чтобы отличать от красного A*)
                symbol = QgsLineSymbol.createSimple({
                    'color': '#1f78b4',
                    'width': '3.0',
                    'penstyle': 'solid',
                    'linecap': 'round',
                    'linejoin': 'round'
                })
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
                
                QgsProject.instance().addMapLayer(layer)
                QgsMessageLog.logMessage(f"GA Маршрут добавлен на карту. Сегментов: {len(self.best_chromosome)}", "RoutingOptimizer", Qgis.Success)
            else:
                QgsMessageLog.logMessage("Не удалось создать векторный слой GA маршрута.", "RoutingOptimizer", Qgis.Critical)
        else:
            err_msg = str(self.exception) if self.exception else "Задача была отменена."
            QgsMessageLog.logMessage(f"Сбой задачи GARouting: {err_msg}", "RoutingOptimizer", Qgis.Critical)