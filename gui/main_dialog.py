# -*- coding: utf-8 -*-
import os
import pandas as pd
import geopandas as gpd
from typing import List, Tuple, Optional
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, 
    QGroupBox, QPushButton, QTableWidget, QTableWidgetItem, 
    QFileDialog, QMessageBox, QHeaderView, QAbstractItemView, QLabel,
    QSpinBox, QDoubleSpinBox, QProgressBar, QTextEdit, QComboBox, QCheckBox
)
from qgis.PyQt.QtCore import Qt
from qgis.gui import QgsProjectionSelectionWidget, QgsMapLayerComboBox
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsPointXY, QgsMessageLog, 
    Qgis, QgsApplication, QgsWkbTypes, QgsMapLayerProxyModel, QgsRasterLayer,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsLineSymbol, QgsSingleSymbolRenderer
)

from .map_tools import PointCaptureTool
from ..core.data_manager import DataManager
from ..utils.crs_utils import is_metric_crs
from ..utils.osm_utils import get_default_weights_table
from ..core.tasks import CostSurfaceTask, HAS_PYROSM, AStarRoutingTask, GARoutingTask

class MainDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.setWindowTitle("Routing Optimizer: Оптимизация трасс")
        self.resize(950, 750)

        self.wells_points: List[Tuple[str, str, float, float, str]] = []
        self.key_points: List[Tuple[str, str, float, float, str]] = []
        self.current_capture_target: Optional[str] = None
        
        self.map_tool = PointCaptureTool(self.canvas)
        self.map_tool.pointCaptured.connect(self.on_point_captured)

        self._init_ui()

    # ... [Метод _init_ui остается точно таким же, как в предыдущем полном коде] ...
    # (Для экономии места я не дублирую его здесь, но в вашем файле он должен быть полным, как в прошлом ответе)
    # Убедитесь, что self.tabs.addTab(self.tab_routing, "3. Оптимизация") присутствует!

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        self.tab_inputs = QWidget()
        layout_inputs = QVBoxLayout(self.tab_inputs)

        self.group_wells, self.combo_wells_layer, self.btn_load_wells_layer, self.btn_load_wells, self.btn_paste_wells, self.btn_capture_wells, self.btn_delete_wells, self.table_wells = \
            self._create_point_group("1. Скважины (Wells Points) - Источники", "wells")
        layout_inputs.addWidget(self.group_wells)

        self.group_keys, self.combo_keys_layer, self.btn_load_keys_layer, self.btn_load_keys, self.btn_paste_keys, self.btn_capture_keys, self.btn_delete_keys, self.table_keys = \
            self._create_point_group("2. Ключевые пункты (ДНС/КНС) - Стоки", "keys")
        layout_inputs.addWidget(self.group_keys)

        group_crs = QGroupBox("3. Системы координат (Требуются метрические!)")
        layout_crs = QVBoxLayout()
        
        crs_widget_layout = QHBoxLayout()
        crs_widget_layout.addWidget(QLabel("Исходная СК точек:"))
        self.src_crs_widget = QgsProjectionSelectionWidget()
        self.src_crs_widget.setCrs(QgsProject.instance().crs())
        crs_widget_layout.addWidget(self.src_crs_widget)
        
        crs_widget_layout.addWidget(QLabel("Целевая СК проекта (метрическая):"))
        self.target_crs_widget = QgsProjectionSelectionWidget()
        self.target_crs_widget.setCrs(QgsProject.instance().crs())
        crs_widget_layout.addWidget(self.target_crs_widget)
        
        layout_crs.addLayout(crs_widget_layout)
        
        self.btn_convert = QPushButton("Конвертировать и добавить все точки на карту")
        self.btn_convert.clicked.connect(self.process_and_add_points)
        layout_crs.addWidget(self.btn_convert)
        
        group_crs.setLayout(layout_crs)
        layout_inputs.addWidget(group_crs)
        layout_inputs.addStretch()
        self.tabs.addTab(self.tab_inputs, "1. Входные данные и СК")

        self.tab_cost = QWidget()
        layout_cost = QVBoxLayout(self.tab_cost)

        group_osm = QGroupBox("1. Источник данных OSM (.pbf)")
        layout_osm = QHBoxLayout()
        self.lbl_pbf = QLabel("Файл не выбран")
        self.lbl_pbf.setStyleSheet("color: gray;")
        self.btn_select_pbf = QPushButton("Выбрать файл .osm.pbf")
        self.btn_select_pbf.clicked.connect(self.select_pbf_file)
        layout_osm.addWidget(self.lbl_pbf)
        layout_osm.addWidget(self.btn_select_pbf)
        group_osm.setLayout(layout_osm)
        layout_cost.addWidget(group_osm)

        # ==========================================
        # Группа: Цифровая модель рельефа (ЦМР / DEM)
        # ==========================================
        group_dem = QGroupBox("2. Учет рельефа (ЦМР / DEM)")
        layout_dem = QVBoxLayout()

        # Чекбокс для включения/выключения
        self.chk_use_dem = QCheckBox("Учитывать рельеф местности при расчете стоимости")
        self.chk_use_dem.setChecked(False)
        self.chk_use_dem.toggled.connect(self.toggle_dem_controls)
        layout_dem.addWidget(self.chk_use_dem)

        # Горизонтальный блок для выбора слоя и веса
        dem_controls = QHBoxLayout()

        # Выпадающий список только с растровыми слоями
        self.combo_dem_layer = QgsMapLayerComboBox()
        self.combo_dem_layer.setFilters(QgsMapLayerProxyModel.RasterLayer) # Показывать только растры!
        self.combo_dem_layer.setAllowEmptyLayer(True)
        self.combo_dem_layer.setPlaceholderText("Выберите растровый слой ЦМР...")
        
        dem_controls.addWidget(QLabel("Слой:"))
        dem_controls.addWidget(self.combo_dem_layer, 1) # Растягивается

        dem_controls.addWidget(QLabel("Штраф за 1° уклона:"))
        self.spin_elev_weight = QDoubleSpinBox()
        self.spin_elev_weight.setRange(0.1, 100.0)
        self.spin_elev_weight.setSingleStep(0.5)
        self.spin_elev_weight.setValue(2.0)
        self.spin_elev_weight.setToolTip("Добавляет стоимость за каждый градус наклона. 0 = игнорировать рельеф.")
        dem_controls.addWidget(self.spin_elev_weight)

        layout_dem.addLayout(dem_controls)
        group_dem.setLayout(layout_dem)
        layout_cost.addWidget(group_dem)

        # Инициализируем состояние элементов управления (выключены по умолчанию)
        self.toggle_dem_controls(False)

        # Группа: Параметры Cost-модели (бывшая группа 2, теперь станет 3)
        group_params = QGroupBox("3. Параметры растеризации OSM")
        layout_params = QHBoxLayout()
        layout_params.addWidget(QLabel("Буфер вокруг точек (м):"))
        self.spin_buffer = QSpinBox()
        self.spin_buffer.setRange(100, 50000)
        self.spin_buffer.setValue(5000)
        layout_params.addWidget(self.spin_buffer)
        
        layout_params.addWidget(QLabel("Разрешение растра (м/пиксель):"))
        self.spin_res = QSpinBox()
        self.spin_res.setRange(1, 100)
        self.spin_res.setValue(10)
        layout_params.addWidget(self.spin_res)
        layout_params.addStretch()
        group_params.setLayout(layout_params)
        layout_cost.addWidget(group_params)

        group_weights = QGroupBox("3. Весовые коэффициенты объектов")
        layout_weights = QVBoxLayout()
        self.table_weights = QTableWidget()
        self.table_weights.setColumnCount(2)
        self.table_weights.setHorizontalHeaderLabels(["OSM Тег (ключ:значение)", "Вес (Стоимость)"])
        self.table_weights.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._populate_weights_table()
        layout_weights.addWidget(self.table_weights)
        group_weights.setLayout(layout_weights)
        layout_cost.addWidget(group_weights)

        group_action = QGroupBox("4. Запуск")
        layout_action = QVBoxLayout()
        self.btn_build_cost = QPushButton("Построить Cost-поверхность")
        self.btn_build_cost.clicked.connect(self.start_cost_surface_task)
        layout_action.addWidget(self.btn_build_cost)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        layout_action.addWidget(self.progress_bar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(150)
        self.log_text.setStyleSheet("background-color: #f0f0f0; font-family: monospace;")
        layout_action.addWidget(self.log_text)
        group_action.setLayout(layout_action)
        layout_cost.addWidget(group_action)
        layout_cost.addStretch()
        self.tabs.addTab(self.tab_cost, "2. Cost-поверхность")

        self.tab_routing = QWidget()
        layout_routing = QVBoxLayout(self.tab_routing)

        group_algo = QGroupBox("1. Выбор алгоритма")
        layout_algo = QVBoxLayout()
        self.combo_algo = QComboBox()
        self.combo_algo.addItems([
            "A* (Жадное построение сети)", 
            "Генетический алгоритм (GA) - Оптимизация топологии"
        ])
        self.combo_algo.currentIndexChanged.connect(self.on_algo_changed)
        layout_algo.addWidget(self.combo_algo)
        group_algo.setLayout(layout_algo)
        layout_routing.addWidget(group_algo)

        self.group_ga_params = QGroupBox("2. Параметры Генетического Алгоритма")
        layout_ga = QVBoxLayout()
        ga_grid = QHBoxLayout()
        
        ga_grid.addWidget(QLabel("Размер популяции:"))
        self.spin_ga_pop = QSpinBox()
        self.spin_ga_pop.setRange(10, 200)
        self.spin_ga_pop.setValue(50)
        ga_grid.addWidget(self.spin_ga_pop)
        
        ga_grid.addWidget(QLabel("Поколений:"))
        self.spin_ga_gen = QSpinBox()
        self.spin_ga_gen.setRange(5, 100)
        self.spin_ga_gen.setValue(20)
        ga_grid.addWidget(self.spin_ga_gen)
        
        ga_grid.addWidget(QLabel("Мутация (0.0-1.0):"))
        self.double_ga_mut = QDoubleSpinBox()
        self.double_ga_mut.setRange(0.0, 1.0)
        self.double_ga_mut.setSingleStep(0.05)
        self.double_ga_mut.setValue(0.2)
        ga_grid.addWidget(self.double_ga_mut)
        
        layout_ga.addLayout(ga_grid)
        self.group_ga_params.setLayout(layout_ga)
        self.group_ga_params.setVisible(False) 
        layout_routing.addWidget(self.group_ga_params)

        group_action_r = QGroupBox("3. Запуск оптимизации")
        layout_action_r = QVBoxLayout()
        self.btn_run_routing = QPushButton("Запустить поиск трассы")
        self.btn_run_routing.clicked.connect(self.start_routing_task)
        layout_action_r.addWidget(self.btn_run_routing)

        self.progress_routing = QProgressBar()
        self.progress_routing.setValue(0)
        self.progress_routing.setTextVisible(True)
        layout_action_r.addWidget(self.progress_routing)

        self.log_routing = QTextEdit()
        self.log_routing.setReadOnly(True)
        self.log_routing.setMaximumHeight(150)
        self.log_routing.setStyleSheet("background-color: #f0f0f0; font-family: monospace;")
        layout_action_r.addWidget(self.log_routing)
        group_action_r.setLayout(layout_action_r)
        layout_routing.addWidget(group_action_r)
        layout_routing.addStretch()
        
        self.tabs.addTab(self.tab_routing, "3. Оптимизация")
        self.tabs.addTab(QWidget(), "4. Результаты (В разработке)")

    def _create_point_group(self, title: str, target_type: str):
        group = QGroupBox(title)
        layout = QVBoxLayout()
        
        layer_layout = QHBoxLayout()
        layer_combo = QgsMapLayerComboBox()
        layer_combo.setFilters(QgsMapLayerProxyModel.PointLayer)
        layer_combo.setAllowEmptyLayer(True)
        layer_combo.setPlaceholderText("Выберите слой с точками...")
        
        btn_load_qgis = QPushButton("📥 Загрузить из слоя QGIS")
        btn_load_qgis.clicked.connect(lambda: self.load_from_qgis_layer(target_type, layer_combo))
        
        layer_layout.addWidget(QLabel("Слой:"))
        layer_layout.addWidget(layer_combo, 1)
        layer_layout.addWidget(btn_load_qgis)
        layout.addLayout(layer_layout)

        btn_layout = QHBoxLayout()
        btn_load = QPushButton("Загрузить из Excel/CSV")
        btn_load.clicked.connect(lambda: self.load_file(target_type))
        
        btn_paste = QPushButton("Вставить из буфера")
        btn_paste.clicked.connect(lambda: self.paste_from_clipboard(target_type))
        
        btn_capture = QPushButton("Выбрать на карте")
        btn_capture.setCheckable(True)
        btn_capture.clicked.connect(lambda checked, t=target_type: self.toggle_capture_mode(checked, t))
        
        btn_delete = QPushButton("Удалить выбранные")
        btn_delete.clicked.connect(lambda: self.delete_selected_points(target_type))
        btn_delete.setStyleSheet("color: #b30000; font-weight: bold;")
        
        btn_layout.addWidget(btn_load)
        btn_layout.addWidget(btn_paste)
        btn_layout.addWidget(btn_capture)
        btn_layout.addWidget(btn_delete)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels(["ID", "Тип", "X", "Y", "Исходная СК"])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        layout.addWidget(table)
        
        group.setLayout(layout)
        return group, layer_combo, btn_load_qgis, btn_load, btn_paste, btn_capture, btn_delete, table

    def load_from_qgis_layer(self, target_type: str, layer_combo: QgsMapLayerComboBox):
        layer = layer_combo.currentLayer()
        if not layer or not layer.isValid():
            QMessageBox.warning(self, "Ошибка", "Пожалуйста, выберите корректный слой из списка.")
            return
        if layer.geometryType() != QgsWkbTypes.PointGeometry:
            QMessageBox.warning(self, "Ошибка", "Выбранный слой не является точечным.")
            return

        target_list = self.wells_points if target_type == 'wells' else self.key_points
        type_name = "Well" if target_type == 'wells' else "KeyPoint"
        
        layer_crs = layer.crs()
        src_crs = layer_crs.authid() if layer_crs.isValid() else "EPSG:4326"
        
        key_point_keywords = ['днс', 'кнс', 'key', 'station', 'станция', 'узел', 'установка']
        well_keywords = ['скважина', 'well', 'куст', 'swab', 'точка']
        
        type_field_idx = -1
        possible_type_names = ['type', 'тип', 'category', 'категория', 'object', 'объект', 'name', 'название']
        fields = layer.fields()
        for name in possible_type_names:
            idx = fields.indexOf(name)
            if idx != -1:
                type_field_idx = idx
                break

        count_added = 0
        count_skipped = 0

        for feat in layer.getFeatures():
            geom = feat.geometry()
            points = geom.asMultiPoint() if geom.isMultipart() else [geom.asPoint()]
            
            is_key_point = False
            is_well = False
            
            if type_field_idx != -1:
                val = feat.attributes()[type_field_idx]
                if val is not None:
                    val_str = str(val).lower()
                    if any(kw in val_str for kw in key_point_keywords):
                        is_key_point = True
                    elif any(kw in val_str for kw in well_keywords):
                        is_well = True

            if target_type == 'wells' and (is_key_point or (type_field_idx != -1 and not is_well)):
                count_skipped += 1
                continue
            elif target_type == 'keys' and (is_well or (type_field_idx != -1 and not is_key_point)):
                count_skipped += 1
                continue

            for pt in points:
                idx_id = fields.indexOf('ID')
                if idx_id != -1 and feat.attributes()[idx_id]:
                    pid = str(feat.attributes()[idx_id])
                else:
                    pid = f"AUTO_{target_type.upper()}_{len(target_list) + count_added + 1:03d}"
                    
                target_list.append((pid, type_name, float(pt.x()), float(pt.y()), src_crs))
                count_added += 1
                
        self._update_table(target_type)
        
        msg = f"Загружено {count_added} точек в '{'Скважины' if target_type == 'wells' else 'Ключевые пункты'}'."
        if count_skipped > 0:
            msg += f"\n(Пропущено {count_skipped} точек, так как они относятся к другому типу)."
        if type_field_idx == -1:
            msg += "\n⚠️ Внимание: В слое не найдено полей 'Тип' или 'Name'. Загружены ВСЕ точки."
        QMessageBox.information(self, "Результат загрузки", msg)

    def toggle_capture_mode(self, checked: bool, target_type: str):
        if target_type == 'wells' and checked:
            self.btn_capture_keys.setChecked(False)
            self.btn_capture_keys.setText("Выбрать на карте")
            self.btn_capture_keys.setStyleSheet("")
        elif target_type == 'keys' and checked:
            self.btn_capture_wells.setChecked(False)
            self.btn_capture_wells.setText("Выбрать на карте")
            self.btn_capture_wells.setStyleSheet("")

        if checked:
            self.current_capture_target = target_type
            self.canvas.setMapTool(self.map_tool)
            btn = self.btn_capture_wells if target_type == 'wells' else self.btn_capture_keys
            btn.setText("Захват активен (кликните)")
            btn.setStyleSheet("background-color: #ffcccc; font-weight: bold;")
        else:
            self.current_capture_target = None
            self.canvas.unsetMapTool(self.map_tool)
            btn = self.btn_capture_wells if target_type == 'wells' else self.btn_capture_keys
            btn.setText("Выбрать на карте")
            btn.setStyleSheet("")

    def on_point_captured(self, point: QgsPointXY):
        if not self.current_capture_target:
            return
        src_crs = self.src_crs_widget.crs().authid()
        if self.current_capture_target == 'wells':
            pid = f"WELL_{len(self.wells_points) + 1:03d}"
            self.wells_points.append((pid, "Well", point.x(), point.y(), src_crs))
            self._update_table('wells')
        elif self.current_capture_target == 'keys':
            pid = f"KEY_{len(self.key_points) + 1:03d}"
            self.key_points.append((pid, "KeyPoint", point.x(), point.y(), src_crs))
            self._update_table('keys')
        self.toggle_capture_mode(False, self.current_capture_target)

    def load_file(self, target_type: str):
        file_path, _ = QFileDialog.getOpenFileName(
            self, f"Выберите файл ({'Скважины' if target_type == 'wells' else 'Ключевые пункты'})", 
            "", "CSV Files (*.csv);;Excel Files (*.xlsx *.xls)"
        )
        if not file_path: return

        file_type = 'csv' if file_path.endswith('.csv') else 'excel'
        try:
            df = DataManager.load_tabular_data(file_path, file_type)
            src_crs = self.src_crs_widget.crs().authid()
            target_list = self.wells_points if target_type == 'wells' else self.key_points
            type_name = "Well" if target_type == 'wells' else "KeyPoint"
            
            for _, row in df.iterrows():
                pid = str(row.get('ID', f"ROW_{_}"))
                x = float(row.get('X', row.get('Lon', row.get('Longitude', 0))))
                y = float(row.get('Y', row.get('Lat', row.get('Latitude', 0))))
                target_list.append((pid, type_name, x, y, src_crs))
                
            self._update_table(target_type)
            QMessageBox.information(self, "Успех", f"Загружено {len(df)} точек.")
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось прочитать файл:\n{e}")

    def paste_from_clipboard(self, target_type: str):
        QMessageBox.information(self, "Информация", "Функция вставки из буфера будет реализована позже.")

    def _update_table(self, target_type: str):
        data_list = self.wells_points if target_type == 'wells' else self.key_points
        table = self.table_wells if target_type == 'wells' else self.table_keys
        table.setRowCount(len(data_list))
        for row, data in enumerate(data_list):
            for col, value in enumerate(data):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                table.setItem(row, col, item)

    def delete_selected_points(self, target_type: str):
        table = self.table_wells if target_type == 'wells' else self.table_keys
        data_list = self.wells_points if target_type == 'wells' else self.key_points
        selected_rows = sorted([idx.row() for idx in table.selectionModel().selectedRows()], reverse=True)

        if not selected_rows:
            QMessageBox.information(self, "Информация", "Выделите строки для удаления (Ctrl+Click).")
            return

        for row_idx in selected_rows:
            del data_list[row_idx]
        self._update_table(target_type)
        QMessageBox.information(self, "Готово", f"Удалено {len(selected_rows)} точек.")

    def process_and_add_points(self):
        if not self.wells_points and not self.key_points:
            QMessageBox.warning(self, "Предупреждение", "Списки точек пусты.")
            return

        target_crs = self.target_crs_widget.crs()
        if not is_metric_crs(target_crs):
            QMessageBox.critical(self, "Ошибка СК", "Целевая система координат должна быть метрической (например, EPSG:3857 или UTM)!")
            return

        try:
            all_points = self.wells_points + self.key_points
            
            # ДИАГНОСТИКА: Проверяем, все ли точки имеют одинаковую исходную СК
            src_crs_set = set(p[4] for p in all_points)
            if len(src_crs_set) > 1:
                QgsMessageLog.logMessage(f"ВНИМАНИЕ: Обнаружены точки с разными исходными СК: {src_crs_set}. Будет использована СК первой точки: {list(src_crs_set)[0]}", "RoutingOptimizer", Qgis.Warning)
                QMessageBox.warning(self, "Предупреждение", f"Точки загружены с разными исходными СК: {src_crs_set}.\nДля корректной конвертации будет использована СК первой точки ({list(src_crs_set)[0]}).\nРекомендуется очистить таблицы и загрузить все точки, предварительно выбрав правильную 'Исходную СК' в виджете.")
            
            src_crs_str = list(src_crs_set)[0]
            
            QgsMessageLog.logMessage(f"DEBUG: Координаты ДО конвертации: {all_points}", "RoutingOptimizer", Qgis.Info)

            gdf = gpd.GeoDataFrame(
                all_points, columns=['id', 'type', 'x', 'y', 'source_crs'],
                geometry=gpd.points_from_xy([p[2] for p in all_points], [p[3] for p in all_points])
            )
            
            gdf.set_crs(src_crs_str, inplace=True)
            target_crs_str = target_crs.authid()
            gdf_converted = gdf.to_crs(target_crs_str)

            converted_points = []
            for idx, row in gdf_converted.iterrows():
                geom = row.geometry
                converted_points.append((row['id'], row['type'], geom.x, geom.y, target_crs_str))

            QgsMessageLog.logMessage(f"DEBUG: Координаты ПОСЛЕ конвертации: {converted_points}", "RoutingOptimizer", Qgis.Info)

            self.wells_points = [p for p in converted_points if p[1] == "Well"]
            self.key_points = [p for p in converted_points if p[1] == "KeyPoint"]
            self._update_table('wells')
            self._update_table('keys')

            layer = DataManager.create_memory_layer(converted_points, target_crs, "Input_Points_Metric")
            if layer:
                self.canvas.zoomToFullExtent()
                QMessageBox.information(self, "Успех", f"Точки конвертированы и добавлены.\nСкважин: {len(self.wells_points)}, Ключевых: {len(self.key_points)}")
        except Exception as e:
            QgsMessageLog.logMessage(f"Ошибка конвертации: {e}", "RoutingOptimizer", Qgis.Critical)
            QMessageBox.critical(self, "Ошибка обработки", f"Произошла ошибка:\n{e}")

    def _populate_weights_table(self):
        weights_data = get_default_weights_table()
        self.table_weights.setRowCount(len(weights_data))
        for row, item in enumerate(weights_data):
            self.table_weights.setItem(row, 0, QTableWidgetItem(item["tag"]))
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setValue(int(item["weight"]))
            self.table_weights.setCellWidget(row, 1, spin)

    def select_pbf_file(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выберите файл OSM .pbf", "", "OSM PBF Files (*.pbf)")
        if file_path:
            self.lbl_pbf.setText(os.path.basename(file_path))
            self.lbl_pbf.setStyleSheet("color: black; font-weight: bold;")
            self._pbf_path = file_path

    def _log(self, message: str):
        self.log_text.append(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] {message}")
        self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())

    def start_cost_surface_task(self):
        if not hasattr(self, '_pbf_path') or not os.path.exists(self._pbf_path):
            QMessageBox.warning(self, "Ошибка", "Пожалуйста, выберите файл .osm.pbf")
            return
        if not self.wells_points and not self.key_points:
            QMessageBox.warning(self, "Ошибка", "Сначала загрузите точки на Вкладке 1.")
            return
        if not HAS_PYROSM:
            QMessageBox.critical(self, "Отсутствует зависимость", "Установите pyrosm: Модули -> Консоль Python -> import subprocess, sys; subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyrosm'])")
            return

        self._log("Подготовка данных...")
        self.btn_build_cost.setEnabled(False)
        self.progress_bar.setValue(0)

        weights_dict = {"default": 1.0}
        for row in range(self.table_weights.rowCount()):
            tag = self.table_weights.item(row, 0).text()
            spin_widget = self.table_weights.cellWidget(row, 1)
            weights_dict[tag] = float(spin_widget.value())

        all_points = self.wells_points + self.key_points
        gdf = gpd.GeoDataFrame(
            all_points, columns=['id', 'type', 'x', 'y', 'source_crs'],
            geometry=gpd.points_from_xy([p[2] for p in all_points], [p[3] for p in all_points])
        )
        gdf.set_crs(self.target_crs_widget.crs().authid(), inplace=True)

        # Получаем путь к ЦМР и вес, если они заданы
        dem_path = None
        elev_weight = 0.0
        
        if self.chk_use_dem.isChecked():
            dem_layer = self.combo_dem_layer.currentLayer()
            if dem_layer and dem_layer.isValid():
                dem_path = dem_layer.source()
                elev_weight = self.spin_elev_weight.value()
            else:
                QMessageBox.warning(self, "Предупреждение", "Чекбокс учета рельефа включен, но слой ЦМР не выбран или некорректен. Рельеф будет проигнорирован.")

        self.cost_task = CostSurfaceTask(
            points_gdf=gdf, 
            pbf_path=self._pbf_path, 
            buffer_meters=float(self.spin_buffer.value()),
            weights_dict=weights_dict, 
            target_crs=self.target_crs_widget.crs(), 
            resolution_m=float(self.spin_res.value()),
            dem_path=dem_path,          # Передаем путь из слоя (или None)
            elev_weight=elev_weight     # Передаем вес (или 0.0)
        )

        self.cost_task.progressChanged.connect(lambda val: self.progress_bar.setValue(int(val)))
        self.cost_task.taskCompleted.connect(self.on_task_completed)
        self.cost_task.taskTerminated.connect(self.on_task_terminated)

        QgsApplication.taskManager().addTask(self.cost_task)
        self._log("Задача добавлена в менеджер. Выполняется в фоне...")

    def on_task_completed(self):
        self.btn_build_cost.setEnabled(True)
        self.progress_bar.setValue(100)
        self._log("✅ Построение Cost-поверхности успешно завершено!")
        QMessageBox.information(self, "Успех", "Cost-поверхность построена и добавлена на карту.")

    def on_task_terminated(self):
        self.btn_build_cost.setEnabled(True)
        self.progress_bar.setValue(0)
        self._log("❌ Ошибка или отмена построения. Проверьте логи QGIS.")

    def on_algo_changed(self, index: int):
        if index == 1: 
            self.group_ga_params.setVisible(True)
        else:
            self.group_ga_params.setVisible(False)

    def _log_routing(self, message: str):
        self.log_routing.append(f"[{pd.Timestamp.now().strftime('%H:%M:%S')}] {message}")
        self.log_routing.verticalScrollBar().setValue(self.log_routing.verticalScrollBar().maximum())

    def start_routing_task(self):
        algo_index = self.combo_algo.currentIndex()
        
        cost_layer = None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == "Cost_Surface" and isinstance(layer, QgsRasterLayer):
                cost_layer = layer
                break

        if not cost_layer or not cost_layer.isValid():
            QMessageBox.warning(self, "Ошибка", "Слой 'Cost_Surface' не найден. Сначала постройте его на Вкладке 2.")
            return

        if not self.wells_points or not self.key_points:
            QMessageBox.warning(self, "Ошибка", "Необходимо загрузить как минимум одну скважину и одну ключевую точку.")
            return

        self._log_routing("Подготовка к поиску трассы...")
        self.btn_run_routing.setEnabled(False)
        self.progress_routing.setValue(0)

        all_nodes = []
        dns_indices = []
        
        for p in self.key_points:
            all_nodes.append((p[0], p[2], p[3]))
            dns_indices.append(len(all_nodes) - 1)
            
        for p in self.wells_points:
            all_nodes.append((p[0], p[2], p[3]))
            
        target_crs = self.target_crs_widget.crs()
        
        # ДИАГНОСТИКА: Логируем точки, которые пойдут в алгоритм
        QgsMessageLog.logMessage(f"DEBUG ГА: Итоговые узлы для маршрутизации (ID, X, Y): {all_nodes}", "RoutingOptimizer", Qgis.Info)
        QgsMessageLog.logMessage(f"DEBUG ГА: Индексы ключевых точек (ДНС): {dns_indices}", "RoutingOptimizer", Qgis.Info)

        if algo_index == 0: # A*
            start_coords = [(p[2], p[3]) for p in self.wells_points]
            end_coords = [(p[2], p[3]) for p in self.key_points]
            self.routing_task = AStarRoutingTask(
                raster_path=cost_layer.source(), start_points=start_coords,
                end_points=end_coords, target_crs=target_crs
            )
        else: # GA
            self._log_routing(f"Запуск ГА: Популяция={self.spin_ga_pop.value()}, Поколений={self.spin_ga_gen.value()}")
            self.routing_task = GARoutingTask(
                raster_path=cost_layer.source(),
                nodes=all_nodes,
                dns_indices=dns_indices,
                target_crs=target_crs,
                pop_size=self.spin_ga_pop.value(),
                generations=self.spin_ga_gen.value(),
                mutation_rate=self.double_ga_mut.value(),
                crossover_rate=0.8
            )

        self.routing_task.progressChanged.connect(lambda val: self.progress_routing.setValue(int(val)))
        self.routing_task.taskCompleted.connect(self.on_routing_completed)
        self.routing_task.taskTerminated.connect(self.on_routing_terminated)

        QgsApplication.taskManager().addTask(self.routing_task)
        self._log_routing("Задача запущена в фоне...")

    def on_routing_completed(self):
        self.btn_run_routing.setEnabled(True)
        self.progress_routing.setValue(100)
        self._log_routing("✅ Поиск трассы успешно завершен! Слой добавлен на карту.")
        QMessageBox.information(self, "Успех", "Оптимальная трасса построена и добавлена на карту.")

    def on_routing_terminated(self):
        self.btn_run_routing.setEnabled(True)
        self.progress_routing.setValue(0)
        self._log_routing("❌ Ошибка или отмена поиска трассы. Проверьте логи QGIS.")

    def closeEvent(self, event):
        if self.canvas.mapTool() == self.map_tool:
            self.canvas.unsetMapTool(self.map_tool)
        super().closeEvent(event)

    def toggle_dem_controls(self, enabled: bool):
        """Включает или отключает элементы управления ЦМР в зависимости от чекбокса."""
        self.combo_dem_layer.setEnabled(enabled)
        self.spin_elev_weight.setEnabled(enabled)