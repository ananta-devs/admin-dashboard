from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from app.formula.types import CellCoord, CellRange


class ASTNode(ABC):
    """Abstract base class for all formula Abstract Syntax Tree nodes."""

    @abstractmethod
    def node_count(self) -> int:
        """Count total AST nodes in this subtree."""
        pass


@dataclass(slots=True)
class LiteralNode(ASTNode):
    value: Any
    literal_type: str  # 'number', 'string', 'boolean'

    def node_count(self) -> int:
        return 1

    def __repr__(self) -> str:
        return f"Literal({self.value!r}:{self.literal_type})"


@dataclass(slots=True)
class CellRefNode(ASTNode):
    coord: CellCoord
    ref: str

    def node_count(self) -> int:
        return 1

    def __repr__(self) -> str:
        return f"CellRef({self.ref})"


@dataclass(slots=True)
class RangeRefNode(ASTNode):
    cell_range: CellRange
    ref: str

    def node_count(self) -> int:
        return 1

    def __repr__(self) -> str:
        return f"RangeRef({self.ref})"


@dataclass(slots=True)
class UnaryOpNode(ASTNode):
    op: str  # '+', '-'
    operand: ASTNode

    def node_count(self) -> int:
        return 1 + self.operand.node_count()

    def __repr__(self) -> str:
        return f"UnaryOp({self.op} {self.operand!r})"


@dataclass(slots=True)
class BinaryOpNode(ASTNode):
    op: str  # '+', '-', '*', '/', '^', '=', '<>', '<', '<=', '>', '>='
    left: ASTNode
    right: ASTNode

    def node_count(self) -> int:
        return 1 + self.left.node_count() + self.right.node_count()

    def __repr__(self) -> str:
        return f"BinaryOp({self.left!r} {self.op} {self.right!r})"


@dataclass(slots=True)
class FunctionCallNode(ASTNode):
    name: str  # 'SUM', 'AVERAGE', 'COUNT', 'MIN', 'MAX', 'IF'
    args: list[ASTNode]

    def node_count(self) -> int:
        return 1 + sum(a.node_count() for a in self.args)

    def __repr__(self) -> str:
        args_repr = ", ".join(repr(a) for a in self.args)
        return f"Function({self.name}({args_repr}))"
