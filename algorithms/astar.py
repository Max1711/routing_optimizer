# -*- coding: utf-8 -*-
import heapq
from typing import List, Tuple, Optional, Dict, Callable
import numpy as np
from osgeo import gdal

class RasterAStar:
    MOVES = [
        (-1, 0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
        (-1, -1, 1.414), (-1, 1, 1.414), (1, -1, 1.414), (1, 1, 1.414)
    ]

    def __init__(self, raster_path: str):
        self.raster_path = raster_path
        self.dataset: Optional[gdal.Dataset] = None
        self.band: Optional[gdal.Band] = None
        self.width = 0
        self.height = 0
        self.geotransform: Optional[Tuple[float, ...]] = None
        self.no_data_value = 9999.0
        self._load_raster_info()

    def _load_raster_info(self):
        self.dataset = gdal.Open(self.raster_path)
        if not self.dataset:
            raise ValueError(f"Не удалось открыть растр: {self.raster_path}")
        self.band = self.dataset.GetRasterBand(1)
        self.width = self.dataset.RasterXSize
        self.height = self.dataset.RasterYSize
        self.geotransform = self.dataset.GetGeoTransform()
        self.no_data_value = self.band.GetNoDataValue() or 9999.0

    def _pixel_to_coord(self, px: int, py: int) -> Tuple[float, float]:
        x = self.geotransform[0] + px * self.geotransform[1] + py * self.geotransform[2]
        y = self.geotransform[3] + px * self.geotransform[4] + py * self.geotransform[5]
        return x, y

    def _coord_to_pixel(self, x: float, y: float) -> Tuple[int, int]:
        px = int((x - self.geotransform[0]) / self.geotransform[1])
        py = int((y - self.geotransform[3]) / self.geotransform[5])
        return px, py

    def _get_cost(self, px: int, py: int) -> float:
        if px < 0 or px >= self.width or py < 0 or py >= self.height:
            return float('inf')
        data = self.band.ReadAsArray(px, py, 1, 1)
        if data is None:
            return float('inf')
        val = float(data[0, 0])
        if val == self.no_data_value:
            return float('inf')
        return val

    def find_path(self, start_coords: List[Tuple[float, float]], end_coords: List[Tuple[float, float]], 
                  progress_callback: Optional[Callable] = None) -> Tuple[Optional[List[Tuple[float, float]]], float]:
        """
        Ищет путь. Возвращает кортеж: (список координат пути, общая стоимость пути).
        """
        if not self.dataset:
            return None, float('inf')

        start_pixels = [self._coord_to_pixel(x, y) for x, y in start_coords]
        end_pixels = set(self._coord_to_pixel(x, y) for x, y in end_coords)

        open_set = []
        heapq.heapify(open_set)
        
        counter = 0
        g_score_init = {}
        for spx, spy in start_pixels:
            cost = self._get_cost(spx, spy)
            if cost != float('inf'):
                heapq.heappush(open_set, (0, counter, (spx, spy)))
                g_score_init[(spx, spy)] = 0.0
                counter += 1

        if not g_score_init:
            return None, float('inf')

        came_from: Dict[Tuple[int, int], Tuple[int, int]] = {}
        g_score: Dict[Tuple[int, int], float] = g_score_init
        
        visited_count = 0

        while open_set:
            if progress_callback and visited_count % 1000 == 0:
                progress_callback(min(95, int((visited_count / 5000) * 100)))

            current_f, _, current_pixel = heapq.heappop(open_set)

            if current_pixel in end_pixels:
                path_pixels = [current_pixel]
                while current_pixel in came_from:
                    current_pixel = came_from[current_pixel]
                    path_pixels.append(current_pixel)
                
                path_pixels.reverse()
                path_coords = [self._pixel_to_coord(px, py) for px, py in path_pixels]
                return path_coords, g_score[current_pixel]

            px, py = current_pixel
            current_g = g_score[current_pixel]

            for dy, dx, move_cost in self.MOVES:
                neighbor = (px + dx, py + dy)
                pixel_cost = self._get_cost(neighbor[0], neighbor[1])
                
                if pixel_cost == float('inf'):
                    continue
                
                tentative_g = current_g + (move_cost * pixel_cost)

                if neighbor not in g_score or tentative_g < g_score[neighbor]:
                    came_from[neighbor] = current_pixel
                    g_score[neighbor] = tentative_g
                    
                    h = min(np.sqrt((neighbor[0] - ex)**2 + (neighbor[1] - ey)**2) for ex, ey in end_pixels)
                    f = tentative_g + h
                    
                    counter += 1
                    heapq.heappush(open_set, (f, counter, neighbor))
            
            visited_count += 1

        return None, float('inf')

    def close(self):
        if self.dataset:
            self.band = None
            self.dataset = None