from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# Standard Formula Errors
REF_ERROR = "#REF!"
DIV_ZERO_ERROR = "#DIV/0!"
VALUE_ERROR = "#VALUE!"
NAME_ERROR = "#NAME?"
CYCLE_ERROR = "#CYCLE!"
NUM_ERROR = "#NUM!"

VALID_ERROR_CODES = {
    REF_ERROR,
    DIV_ZERO_ERROR,
    VALUE_ERROR,
    NAME_ERROR,
    CYCLE_ERROR,
    NUM_ERROR,
}


class FormulaError(Exception):
    """Structured calculation error according to spreadsheet specifications."""

    def __init__(self, code: str, message: str = ""):
        self.code = code
        self.message = message or code
        super().__init__(f"{code}: {self.message}")


@dataclass(frozen=True, slots=True)
class CellCoord:
    """Zero-indexed coordinates for a sheet cell (row, col)."""

    row: int
    col: int

    def __repr__(self) -> str:
        return f"CellCoord(row={self.row}, col={self.col}, ref='{format_cell_ref(self)}')"


@dataclass(frozen=True, slots=True)
class CellRange:
    """Bounding box for a contiguous rectangular cell range."""

    start_row: int
    start_col: int
    end_row: int
    end_col: int

    def cells(self) -> list[CellCoord]:
        """Generate all cell coordinates contained within the range in row-major order."""
        return [
            CellCoord(r, c)
            for r in range(self.start_row, self.end_row + 1)
            for c in range(self.start_col, self.end_col + 1)
        ]

    def count(self) -> int:
        """Total number of cells within the range."""
        return (self.end_row - self.start_row + 1) * (self.end_col - self.start_col + 1)


# Regex for single cell reference, e.g. A1, B2, Z100, AA10, XFD1048576
CELL_REF_PATTERN = re.compile(r"^([A-Za-z]+)([1-9][0-9]*)$")


def col_to_letter(col_idx: int) -> str:
    """Convert zero-based column index to spreadsheet column letter (0 -> A, 25 -> Z, 26 -> AA)."""
    if col_idx < 0:
        raise ValueError(f"Column index must be non-negative: {col_idx}")

    result = []
    n = col_idx + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result.append(chr(65 + remainder))
    return "".join(reversed(result))


def letter_to_col(letters: str) -> int:
    """Convert spreadsheet column letter to zero-based column index ('A' -> 0, 'Z' -> 25, 'AA' -> 26)."""
    letters = letters.strip().upper()
    if not letters.isalpha():
        raise ValueError(f"Invalid column letters: {letters}")

    col = 0
    for char in letters:
        col = col * 26 + (ord(char) - 64)
    return col - 1


def parse_cell_ref(ref: str) -> CellCoord:
    """Parse standard cell reference (e.g. 'A1', 'B5', 'AA10') into 0-indexed CellCoord."""
    match = CELL_REF_PATTERN.match(ref.strip())
    if not match:
        raise FormulaError(REF_ERROR, f"Invalid cell reference: '{ref}'")

    col_str, row_str = match.groups()
    col = letter_to_col(col_str)
    row = int(row_str) - 1
    return CellCoord(row=row, col=col)


def format_cell_ref(coord: CellCoord) -> str:
    """Format CellCoord into standard spreadsheet reference (e.g. CellCoord(0, 0) -> 'A1')."""
    return f"{col_to_letter(coord.col)}{coord.row + 1}"


def parse_range_ref(range_str: str) -> CellRange:
    """Parse range reference (e.g. 'A1:B10') into normalized CellRange."""
    parts = range_str.strip().split(":")
    if len(parts) != 2:
        raise FormulaError(REF_ERROR, f"Invalid range format: '{range_str}'")

    c1 = parse_cell_ref(parts[0])
    c2 = parse_cell_ref(parts[1])

    start_row = min(c1.row, c2.row)
    end_row = max(c1.row, c2.row)
    start_col = min(c1.col, c2.col)
    end_col = max(c1.col, c2.col)

    return CellRange(
        start_row=start_row,
        start_col=start_col,
        end_row=end_row,
        end_col=end_col,
    )


@dataclass
class FormulaValue:
    """Typed evaluation result of a cell formula."""

    value: Any
    type: str  # 'number', 'string', 'boolean', 'error', 'blank'
    error_code: str | None = None

    @classmethod
    def from_error(cls, error_code: str) -> FormulaValue:
        return cls(value=error_code, type="error", error_code=error_code)

    @classmethod
    def from_number(cls, n: float | int) -> FormulaValue:
        return cls(value=n, type="number")

    @classmethod
    def from_string(cls, s: str) -> FormulaValue:
        return cls(value=s, type="string")

    @classmethod
    def from_boolean(cls, b: bool) -> FormulaValue:
        return cls(value=b, type="boolean")

    @classmethod
    def blank(cls) -> FormulaValue:
        return cls(value=None, type="blank")
