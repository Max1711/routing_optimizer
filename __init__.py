# -*- coding: utf-8 -*-
"""
Точка входа для плагина Routing Optimizer.
"""
from .plugin import RoutingOptimizerPlugin

def classFactory(iface):
    """
    Инициализация плагина QGIS.
    
    :param iface: Экземпляр QgisInterface
    :type iface: QgisInterface
    :return: Экземпляр главного класса плагина
    :rtype: RoutingOptimizerPlugin
    """
    return RoutingOptimizerPlugin(iface)