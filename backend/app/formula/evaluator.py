from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from app.formula.ast import (
    ASTNode,
    BinaryOpNode,
    CellRefNode,
    FunctionCallNode,
    LiteralNode,
    RangeRefNode,
    UnaryOpNode,
)
from app.formula.types import (
    DIV_ZERO_ERROR,
    NAME_ERROR,
    NUM_ERROR,
    VALID_ERROR_CODES,
    VALUE_ERROR,
    CellCoord,
    FormulaError,
    FormulaValue,
)

CellGetter = Callable[[CellCoord], Any]


class Evaluator:
    """Evaluates an AST with a cell lookup context and returns typed results or errors."""

    def __init__(self, cell_getter: CellGetter) -> None:
        self.cell_getter = cell_getter

    def evaluate(self, node: ASTNode) -> FormulaValue:
        try:
            raw = self._eval_node(node)
            if isinstance(raw, FormulaValue):
                return raw
            if isinstance(raw, bool):
                return FormulaValue.from_boolean(raw)
            if isinstance(raw, (int, float)):
                if math.isnan(raw) or math.isinf(raw):
                    return FormulaValue.from_error(NUM_ERROR)
                return FormulaValue.from_number(raw)
            if isinstance(raw, str):
                if raw in VALID_ERROR_CODES:
                    return FormulaValue.from_error(raw)
                return FormulaValue.from_string(raw)
            if raw is None:
                return FormulaValue.blank()
            return FormulaValue.from_string(str(raw))
        except FormulaError as e:
            return FormulaValue.from_error(e.code)
        except ZeroDivisionError:
            return FormulaValue.from_error(DIV_ZERO_ERROR)
        except OverflowError:
            return FormulaValue.from_error(NUM_ERROR)
        except Exception:
            return FormulaValue.from_error(VALUE_ERROR)

    def _eval_node(self, node: ASTNode) -> Any:
        if isinstance(node, LiteralNode):
            return node.value

        if isinstance(node, CellRefNode):
            val = self.cell_getter(node.coord)
            if isinstance(val, str) and val in VALID_ERROR_CODES:
                raise FormulaError(val)
            return val

        if isinstance(node, RangeRefNode):
            # In value context (outside of range functions), top-left cell is used
            cells = node.cell_range.cells()
            if not cells:
                return None
            return self.cell_getter(cells[0])

        if isinstance(node, UnaryOpNode):
            val = self._eval_node(node.operand)
            num = self._coerce_number(val)
            if node.op == "-":
                return -num
            return num

        if isinstance(node, BinaryOpNode):
            return self._eval_binary(node.op, node.left, node.right)

        if isinstance(node, FunctionCallNode):
            return self._eval_function(node.name, node.args)

        raise FormulaError(VALUE_ERROR, f"Unknown AST node type: {type(node)}")

    # --------------------------------------------------------------------------
    # Binary Operator Evaluation
    # --------------------------------------------------------------------------

    def _eval_binary(self, op: str, left_node: ASTNode, right_node: ASTNode) -> Any:
        left_val = self._eval_node(left_node)
        right_val = self._eval_node(right_node)

        # Propagate calculation errors immediately
        if isinstance(left_val, str) and left_val in VALID_ERROR_CODES:
            raise FormulaError(left_val)
        if isinstance(right_val, str) and right_val in VALID_ERROR_CODES:
            raise FormulaError(right_val)

        # Comparisons: =, <>, <, <=, >, >=
        if op in ("=", "<>", "<", "<=", ">", ">="):
            return self._eval_comparison(op, left_val, right_val)

        # Arithmetic operations: +, -, *, /, ^
        left_num = self._coerce_number(left_val)
        right_num = self._coerce_number(right_val)

        if op == "+":
            return left_num + right_num
        if op == "-":
            return left_num - right_num
        if op == "*":
            return left_num * right_num
        if op == "/":
            if right_num == 0:
                raise FormulaError(DIV_ZERO_ERROR)
            return left_num / right_num
        if op == "^":
            try:
                res = math.pow(left_num, right_num)
                if math.isnan(res) or math.isinf(res):
                    raise FormulaError(NUM_ERROR)
                return res
            except (ValueError, OverflowError) as err:
                raise FormulaError(NUM_ERROR) from err

        raise FormulaError(VALUE_ERROR, f"Unknown binary operator '{op}'")

    def _eval_comparison(self, op: str, left: Any, right: Any) -> bool:
        # Normalize types for comparison: convert both to numbers if possible
        l_norm = (
            0
            if left is None and isinstance(right, (int, float))
            else ("" if left is None else left)
        )
        r_norm = (
            0
            if right is None and isinstance(left, (int, float))
            else ("" if right is None else right)
        )

        # Check if both can be compared as numbers
        left_val: Any = l_norm
        right_val: Any = r_norm
        try:
            lf = (
                float(l_norm)
                if isinstance(l_norm, (int, float, str)) and not isinstance(l_norm, bool)
                else None
            )
            rf = (
                float(r_norm)
                if isinstance(r_norm, (int, float, str)) and not isinstance(r_norm, bool)
                else None
            )
            if lf is not None and rf is not None:
                left_val, right_val = lf, rf
        except (ValueError, TypeError):
            pass

        # String case-insensitive comparison for spreadsheet standard
        if isinstance(left_val, str) and isinstance(right_val, str):
            left_cmp: Any = left_val.lower()
            right_cmp: Any = right_val.lower()
        else:
            left_cmp, right_cmp = left_val, right_val

        if op == "=":
            return bool(left_cmp == right_cmp)
        if op == "<>":
            return bool(left_cmp != right_cmp)
        if op == "<":
            return bool(left_cmp < right_cmp)
        if op == "<=":
            return bool(left_cmp <= right_cmp)
        if op == ">":
            return bool(left_cmp > right_cmp)
        if op == ">=":
            return bool(left_cmp >= right_cmp)

        return False

    # --------------------------------------------------------------------------
    # Function Evaluation
    # --------------------------------------------------------------------------

    def _eval_function(self, name: str, args: list[ASTNode]) -> Any:
        name_upper = name.upper()

        if name_upper == "IF":
            if len(args) not in (2, 3):
                raise FormulaError(VALUE_ERROR, "IF requires 2 or 3 arguments")
            cond_val = self._eval_node(args[0])
            if isinstance(cond_val, str) and cond_val in VALID_ERROR_CODES:
                raise FormulaError(cond_val)
            is_true = bool(cond_val)
            if is_true:
                return self._eval_node(args[1])
            elif len(args) == 3:
                return self._eval_node(args[2])
            return False

        # Range aggregate functions: SUM, AVERAGE, COUNT, MIN, MAX
        if name_upper in ("SUM", "AVERAGE", "COUNT", "MIN", "MAX"):
            flat_numbers = self._flatten_numbers(args)

            if name_upper == "SUM":
                return sum(flat_numbers) if flat_numbers else 0

            if name_upper == "AVERAGE":
                if not flat_numbers:
                    raise FormulaError(DIV_ZERO_ERROR)
                return sum(flat_numbers) / len(flat_numbers)

            if name_upper == "COUNT":
                return len(flat_numbers)

            if name_upper == "MIN":
                if not flat_numbers:
                    return 0
                return min(flat_numbers)

            if name_upper == "MAX":
                if not flat_numbers:
                    return 0
                return max(flat_numbers)

        raise FormulaError(NAME_ERROR, f"Unsupported function '{name}'")

    def _flatten_numbers(self, args: list[ASTNode]) -> list[float]:
        numbers: list[float] = []
        for arg in args:
            if isinstance(arg, RangeRefNode):
                # Range: expand all cells and extract numeric values, ignoring text/blanks
                for cell in arg.cell_range.cells():
                    val = self.cell_getter(cell)
                    if isinstance(val, str) and val in VALID_ERROR_CODES:
                        raise FormulaError(val)
                    num = self._try_coerce_number(val)
                    if num is not None:
                        numbers.append(num)
            else:
                # Direct expression/argument
                val = self._eval_node(arg)
                if isinstance(val, str) and val in VALID_ERROR_CODES:
                    raise FormulaError(val)
                num = self._coerce_number(val)
                numbers.append(num)
        return numbers

    # --------------------------------------------------------------------------
    # Type Coercion Helpers
    # --------------------------------------------------------------------------

    def _coerce_number(self, val: Any) -> float:
        if val is None or val == "":
            return 0.0
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            val_clean = val.strip()
            if not val_clean:
                return 0.0
            try:
                return float(val_clean)
            except ValueError as err:
                raise FormulaError(VALUE_ERROR, f"Cannot convert string '{val}' to number") from err
        raise FormulaError(
            VALUE_ERROR, f"Unsupported value type for numeric operation: {type(val)}"
        )

    def _try_coerce_number(self, val: Any) -> float | None:
        if val is None or val == "":
            return None
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val.strip())
            except ValueError:
                return None
        return None
