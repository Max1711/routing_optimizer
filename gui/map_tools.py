# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import pyqtSignal
from qgis.gui import QgsMapToolEmitPoint
from qgis.core import QgsPointXY

class PointCaptureTool(QgsMapToolEmitPoint):
    """
    Инструмент карты для захвата точек кликом мыши.
    """
    pointCaptured = pyqtSignal(QgsPointXY)

    def __init__(self, canvas):
        super().__init__(canvas)
        self.canvas = canvas

    def canvasReleaseEvent(self, event):
        """Обрабатывает отпускание кнопки мыши для получения координат."""
        point = self.toMapCoordinates(event.pos())
        if point:
            self.pointCaptured.emit(point)