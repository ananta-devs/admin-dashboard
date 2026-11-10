from __future__ import annotations

from collections import defaultdict, deque

from app.formula.ast import (
    ASTNode,
    BinaryOpNode,
    CellRefNode,
    FunctionCallNode,
    RangeRefNode,
    UnaryOpNode,
)
from app.formula.types import CYCLE_ERROR, FormulaError, format_cell_ref


def extract_precedents(node: ASTNode) -> set[str]:
    """Extract all cell references that an AST directly depends upon."""
    refs: set[str] = set()

    def _walk(n: ASTNode) -> None:
        if isinstance(n, CellRefNode):
            refs.add(n.ref.upper())
        elif isinstance(n, RangeRefNode):
            for c in n.cell_range.cells():
                refs.add(format_cell_ref(c).upper())
        elif isinstance(n, UnaryOpNode):
            _walk(n.operand)
        elif isinstance(n, BinaryOpNode):
            _walk(n.left)
            _walk(n.right)
        elif isinstance(n, FunctionCallNode):
            for arg in n.args:
                _walk(arg)

    _walk(node)
    return refs


class DependencyGraph:
    """Directed graph tracking spreadsheet formula dependencies.

    - precedents[cell] = set of cells that 'cell' depends upon (inputs)
    - dependents[cell] = set of cells that depend upon 'cell' (downstream)
    """

    def __init__(self) -> None:
        self.precedents: dict[str, set[str]] = defaultdict(set)
        self.dependents: dict[str, set[str]] = defaultdict(set)

    def set_dependencies(self, cell_ref: str, precedent_refs: set[str]) -> None:
        """Register or update the precedent set for a given dependent cell."""
        cell = cell_ref.upper()

        # Remove old forward references
        old_precedents = self.precedents.get(cell, set())
        for old_p in old_precedents:
            self.dependents[old_p].discard(cell)
            if not self.dependents[old_p]:
                del self.dependents[old_p]

        # Register new forward and backward references
        clean_precedents = {p.upper() for p in precedent_refs}
        if clean_precedents:
            self.precedents[cell] = clean_precedents
            for p in clean_precedents:
                self.dependents[p].add(cell)
        else:
            self.precedents.pop(cell, None)

    def remove_cell(self, cell_ref: str) -> None:
        """Remove a formula cell and its dependency links from the graph."""
        self.set_dependencies(cell_ref, set())

    def get_dependents(self, cell_ref: str) -> set[str]:
        """Return the immediate downstream cells that depend on cell_ref."""
        return set(self.dependents.get(cell_ref.upper(), set()))

    def get_all_downstream(self, modified_cells: list[str]) -> set[str]:
        """Return all downstream cells transitively affected by modifying the given cells."""
        visited: set[str] = set()
        queue: deque[str] = deque([c.upper() for c in modified_cells])

        while queue:
            curr = queue.popleft()
            for dep in self.dependents.get(curr, set()):
                if dep not in visited:
                    visited.add(dep)
                    queue.append(dep)

        return visited

    def detect_cycle_from(self, start_cell: str) -> list[str] | None:
        """Check if start_cell is part of a circular dependency loop.

        Returns the cycle path [A1, B1, A1] if found, or None.
        """
        start = start_cell.upper()
        visited: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> list[str] | None:
            if node in path:
                cycle_start = path.index(node)
                return path[cycle_start:] + [node]
            if node in visited:
                return None

            visited.add(node)
            path.append(node)

            for nxt in self.precedents.get(node, set()):
                res = dfs(nxt)
                if res is not None:
                    return res

            path.pop()
            return None

        return dfs(start)

    def get_recalculation_order(self, modified_cells: list[str]) -> list[str]:
        """Determine topological recalculation order for all downstream formula cells.

        Detects cycles and raises FormulaError(CYCLE_ERROR) if an affected cell
        is caught in a circular reference loop.
        """
        downstream = self.get_all_downstream(modified_cells)
        if not downstream:
            return []

        # Check for cycles among downstream nodes
        for cell in downstream:
            cycle = self.detect_cycle_from(cell)
            if cycle is not None:
                raise FormulaError(
                    CYCLE_ERROR, f"Circular reference detected: {' -> '.join(cycle)}"
                )

        # Build in-degree counts strictly within the downstream subgraph
        in_degree: dict[str, int] = {c: 0 for c in downstream}
        for cell in downstream:
            for prec in self.precedents.get(cell, set()):
                if prec in downstream:
                    in_degree[cell] += 1

        # Queue nodes with in-degree 0 (no uncalculated dependencies within downstream)
        queue = deque([c for c, deg in in_degree.items() if deg == 0])
        order: list[str] = []

        while queue:
            curr = queue.popleft()
            order.append(curr)

            for dep in self.dependents.get(curr, set()):
                if dep in downstream:
                    in_degree[dep] -= 1
                    if in_degree[dep] == 0:
                        queue.append(dep)

        if len(order) != len(downstream):
            raise FormulaError(CYCLE_ERROR, "Circular reference detected in calculation order")

        return order
