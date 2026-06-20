# -*- coding: utf-8 -*-
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
import os

from .gui.main_dialog import MainDialog

class RoutingOptimizerPlugin:
    """Главный класс плагина QGIS."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.dialog = None

    def initGui(self):
        """Создание элементов интерфейса плагина."""
        icon_path = os.path.join(self.plugin_dir, 'resources', 'icon.png')
        # Если иконки нет, используем стандартную, чтобы не ломать загрузку
        if not os.path.exists(icon_path):
            icon_path = ":/images/themes/default/mIconLineLayer.svg"

        self.action = QAction(QIcon(icon_path), "Routing Optimizer", self.iface.mainWindow())
        self.action.triggered.connect(self.run)

        # Добавляем в меню Plugins
        self.iface.addPluginToMenu("&Routing Optimizer", self.action)
        # Добавляем на панель инструментов
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        """Удаление элементов интерфейса при выгрузке плагина."""
        self.iface.removePluginMenu("&Routing Optimizer", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.dialog:
            self.dialog.close()
            self.dialog = None

    def run(self):
        """Запуск диалогового окна плагина."""
        if self.dialog is None:
            self.dialog = MainDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()