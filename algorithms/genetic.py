# -*- coding: utf-8 -*-
import random
from typing import List, Tuple, Dict, Optional, Callable
from ..algorithms.astar import RasterAStar

class NetworkGA:
    """
    Генетический алгоритм для оптимизации топологии сети трубопроводов.
    """
    def __init__(self, nodes: List[Tuple[str, float, float]], dns_indices: List[int], 
                 astar_router: RasterAStar, pop_size: int = 50, generations: int = 20,
                 mutation_rate: float = 0.2, crossover_rate: float = 0.8,
                 progress_callback: Optional[Callable] = None):
        self.nodes = nodes  # Список кортежей: (ID, X, Y)
        self.dns_indices = set(dns_indices)
        self.astar = astar_router
        self.pop_size = pop_size
        self.generations = generations
        self.mutation_rate = mutation_rate
        self.crossover_rate = crossover_rate
        self.progress_callback = progress_callback
        
        # Кэш стоимостей путей, чтобы не вызывать A* многократно для одних и тех же пар
        self.cost_cache: Dict[Tuple[int, int], Tuple[float, List[Tuple[float, float]]]] = {}
        
        self.population: List[List[Tuple[int, int]]] = []
        self.fitness_scores: List[float] = []
        self.best_chromosome: Optional[List[Tuple[int, int]]] = None
        self.best_fitness = float('inf')

    def _get_path_cost(self, idx1: int, idx2: int) -> Tuple[float, List[Tuple[float, float]]]:
        """Получает стоимость и путь из кэша или рассчитывает через A*."""
        pair = tuple(sorted((idx1, idx2)))
        if pair in self.cost_cache:
            return self.cost_cache[pair]
        
        p1 = (self.nodes[idx1][1], self.nodes[idx1][2])
        p2 = (self.nodes[idx2][1], self.nodes[idx2][2])
        
        path, cost = self.astar.find_path([p1], [p2])
        if path is None:
            cost = float('inf')
            path = []
            
        self.cost_cache[pair] = (cost, path)
        return cost, path

    def _is_valid_tree(self, chromosome: List[Tuple[int, int]]) -> bool:
        """Проверяет, является ли хромосома связным деревом, соединяющим все узлы с ДНС."""
        if len(chromosome) != len(self.nodes) - 1:
            return False # В дереве N узлов должно быть N-1 ребро
            
        # Строим список смежности
        adj = {i: [] for i in range(len(self.nodes))}
        for u, v in chromosome:
            adj[u].append(v)
            adj[v].append(u)
            
        # BFS от первой ДНС
        start_node = next(iter(self.dns_indices))
        visited = set([start_node])
        queue = [start_node]
        
        while queue:
            current = queue.pop(0)
            for neighbor in adj[current]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(neighbor)
                    
        return len(visited) == len(self.nodes)

    def _calculate_fitness(self, chromosome: List[Tuple[int, int]]) -> float:
        """Рассчитывает общую стоимость сети. Возвращает inf, если дерево невалидно."""
        if not self._is_valid_tree(chromosome):
            return float('inf')
        
        total_cost = 0.0
        for u, v in chromosome:
            cost, _ = self._get_path_cost(u, v)
            total_cost += cost
            if total_cost == float('inf'):
                break
        return total_cost

    def _generate_elite(self) -> List[Tuple[int, int]]:
        """Генерирует хромосому с помощью жадного алгоритма (как в Этапе 3)."""
        connected = list(self.dns_indices)
        unconnected = [i for i in range(len(self.nodes)) if i not in self.dns_indices]
        edges = []
        
        while unconnected:
            best_edge = None
            min_cost = float('inf')
            node_to_connect = None
            
            for u in unconnected:
                for v in connected:
                    cost, _ = self._get_path_cost(u, v)
                    if cost < min_cost:
                        min_cost = cost
                        best_edge = (u, v)
                        node_to_connect = u
                        
            if best_edge:
                edges.append(best_edge)
                connected.append(node_to_connect)
                unconnected.remove(node_to_connect)
            else:
                break # Изолированный узел
        return edges

    def _generate_random(self) -> List[Tuple[int, int]]:
        """Генерирует случайное связное дерево."""
        # Используем алгоритм случайного дерева Прюфера или простой рандомизированный рост
        connected = list(self.dns_indices)
        unconnected = [i for i in range(len(self.nodes)) if i not in self.dns_indices]
        edges = []
        
        while unconnected:
            u = random.choice(unconnected)
            v = random.choice(connected)
            edges.append((u, v))
            connected.append(u)
            unconnected.remove(u)
        return edges

    def _mutate(self, chromosome: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """Мутация: отключает случайную скважину и подключает к новому случайному узлу."""
        new_chromosome = list(chromosome)
        if len(new_chromosome) < 2:
            return new_chromosome
            
        # Выбираем случайное ребро, где хотя бы один узел не является ДНС
        non_dns_edges = [e for e in new_chromosome if e[0] not in self.dns_indices or e[1] not in self.dns_indices]
        if not non_dns_edges:
            return new_chromosome
            
        edge_to_remove = random.choice(non_dns_edges)
        new_chromosome.remove(edge_to_remove)
        
        # Определяем, какой узел отключился (тот, что не ДНС, или случайный из ребра)
        u, v = edge_to_remove
        disconnected_node = u if u not in self.dns_indices else v
        
        # Находим новые возможные подключения
        possible_targets = [i for i in range(len(self.nodes)) if i != disconnected_node]
        random.shuffle(possible_targets)
        
        for target in possible_targets:
            test_chromosome = new_chromosome + [(disconnected_node, target)]
            if self._is_valid_tree(test_chromosome):
                return test_chromosome
                
        return chromosome # Если не удалось, возвращаем как было

    def _crossover(self, parent1: List[Tuple[int, int]], parent2: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        """Кроссовер: объединяет ребра родителей, избегая циклов."""
        all_edges = list(set(parent1 + parent2))
        random.shuffle(all_edges)
        
        child_edges = []
        # Используем простую проверку циклов через DFS/Union-Find концепцию
        # Для простоты: добавляем ребра, пока не наберем N-1 и дерево валидно
        
        # Попробуем построить дерево из случайной подвыборки объединенных ребер
        for _ in range(10): # 10 попыток
            random.shuffle(all_edges)
            temp_edges = []
            connected = set(self.dns_indices)
            unconnected = set(i for i in range(len(self.nodes)) if i not in self.dns_indices)
            
            # Сначала добавляем ребра, соединяющие unconnected с connected
            for u, v in all_edges:
                if (u in connected and v in unconnected) or (v in connected and u in unconnected):
                    temp_edges.append((u, v))
                    connected.add(u)
                    connected.add(v)
                    if u in unconnected: unconnected.remove(u)
                    if v in unconnected: unconnected.remove(v)
                    
            if self._is_valid_tree(temp_edges):
                return temp_edges
                
        # Fallback: если кроссовер не дал валидного дерева, возвращаем мутацию родителя 1
        return self._mutate(parent1)

    def run(self) -> Tuple[Optional[List[Tuple[int, int]]], float]:
        """Запускает эволюционный процесс."""
        # 1. Инициализация популяции
        elite_count = max(1, int(self.pop_size * 0.3))
        self.population = [self._generate_elite() for _ in range(elite_count)]
        self.population += [self._generate_random() for _ in range(self.pop_size - elite_count)]
        
        for chrom in self.population:
            self.fitness_scores.append(self._calculate_fitness(chrom))
            
        best_idx = self.fitness_scores.index(min(self.fitness_scores))
        self.best_fitness = self.fitness_scores[best_idx]
        self.best_chromosome = list(self.population[best_idx])

        # 2. Эволюция
        for gen in range(self.generations):
            if self.progress_callback:
                # Прогресс от 10% до 90%
                pct = 10 + int((gen / self.generations) * 80)
                self.progress_callback(pct)

            new_population = []
            new_fitness = []
            
            # Сортируем по фитнесу (лучшие первые)
            paired = sorted(zip(self.fitness_scores, self.population))
            
            # Элитаризм: сохраняем лучших 10%
            elite_save = max(1, int(self.pop_size * 0.1))
            for i in range(elite_save):
                new_population.append(list(paired[i][1]))
                new_fitness.append(paired[i][0])
                
            while len(new_population) < self.pop_size:
                # Турнирный отбор
                tournament = random.sample(range(self.pop_size), 3)
                tournament.sort(key=lambda x: self.fitness_scores[x])
                parent1 = self.population[tournament[0]]
                
                tournament = random.sample(range(self.pop_size), 3)
                tournament.sort(key=lambda x: self.fitness_scores[x])
                parent2 = self.population[tournament[0]]
                
                if random.random() < self.crossover_rate:
                    child = self._crossover(parent1, parent2)
                else:
                    child = list(parent1)
                    
                if random.random() < self.mutation_rate:
                    child = self._mutate(child)
                    
                new_population.append(child)
                new_fitness.append(self._calculate_fitness(child))
                
            self.population = new_population
            self.fitness_scores = new_fitness
            
            current_best_idx = self.fitness_scores.index(min(self.fitness_scores))
            if self.fitness_scores[current_best_idx] < self.best_fitness:
                self.best_fitness = self.fitness_scores[current_best_idx]
                self.best_chromosome = list(self.population[current_best_idx])

        if self.progress_callback:
            self.progress_callback(100)
            
        return self.best_chromosome, self.best_fitness