# DFS и BFS (RU)

Обход графа: поиск в глубину (DFS) и поиск в ширину (BFS).

## DFS — рекурсивная запись

def dfs(node, visited):
    if node in visited:
        return
    visited.add(node)
    for neighbor in node.neighbors:
        dfs(neighbor, visited)

## BFS — очередь

def bfs(start):
    visited = {start}
    queue = [start]
    while queue:
        node = queue.pop(0)
        for neighbor in node.neighbors:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
