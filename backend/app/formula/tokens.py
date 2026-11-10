from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

from app.formula.types import FormulaError, parse_cell_ref, parse_range_ref


class TokenType(Enum):
    NUMBER = auto()
    STRING = auto()
    BOOLEAN = auto()
    CELL_REF = auto()
    RANGE_REF = auto()
    FUNCTION = auto()
    OPERATOR = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    EOF = auto()


@dataclass(slots=True)
class Token:
    type: TokenType
    value: Any
    pos: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, pos={self.pos})"


SUPPORTED_FUNCTIONS = {"SUM", "AVERAGE", "COUNT", "MIN", "MAX", "IF"}
MAX_FORMULA_LENGTH = 1000


class Tokenizer:
    """Deterministic lexical scanner for spreadsheet formulas."""

    def __init__(self, formula_text: str) -> None:
        if len(formula_text) > MAX_FORMULA_LENGTH:
            raise FormulaError(
                "#NUM!",
                f"Formula length ({len(formula_text)}) exceeds limit of {MAX_FORMULA_LENGTH}",
            )

        text = formula_text.strip()
        if text.startswith("="):
            text = text[1:].strip()
        self.text = text
        self.len = len(text)
        self.pos = 0

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while self.pos < self.len:
            c = self.text[self.pos]

            # Whitespace
            if c.isspace():
                self.pos += 1
                continue

            # Number literal (digits and optional decimal)
            if c.isdigit() or (c == "." and self._peek_digit()):
                tokens.append(self._read_number())
                continue

            # String literal enclosed in double quotes "hello"
            if c == '"':
                tokens.append(self._read_string())
                continue

            # Parentheses, Comma
            if c == "(":
                tokens.append(Token(TokenType.LPAREN, "(", self.pos))
                self.pos += 1
                continue
            if c == ")":
                tokens.append(Token(TokenType.RPAREN, ")", self.pos))
                self.pos += 1
                continue
            if c == ",":
                tokens.append(Token(TokenType.COMMA, ",", self.pos))
                self.pos += 1
                continue

            # Multi-character or single-character operators
            # Comparisons: <=, >=, <>, =, <, >
            if c == "<":
                start = self.pos
                if self._peek() == "=":
                    self.pos += 2
                    tokens.append(Token(TokenType.OPERATOR, "<=", start))
                elif self._peek() == ">":
                    self.pos += 2
                    tokens.append(Token(TokenType.OPERATOR, "<>", start))
                else:
                    self.pos += 1
                    tokens.append(Token(TokenType.OPERATOR, "<", start))
                continue

            if c == ">":
                start = self.pos
                if self._peek() == "=":
                    self.pos += 2
                    tokens.append(Token(TokenType.OPERATOR, ">=", start))
                else:
                    self.pos += 1
                    tokens.append(Token(TokenType.OPERATOR, ">", start))
                continue

            if c == "=":
                tokens.append(Token(TokenType.OPERATOR, "=", self.pos))
                self.pos += 1
                continue

            # Arithmetic operators: +, -, *, /, ^
            if c in "+-*/^":
                tokens.append(Token(TokenType.OPERATOR, c, self.pos))
                self.pos += 1
                continue

            # Identifiers: Function name, Boolean, or Cell Reference / Range Reference
            if c.isalpha() or c == "$":
                tokens.append(self._read_identifier())
                continue

            raise FormulaError("#VALUE!", f"Unexpected character '{c}' at position {self.pos}")

        tokens.append(Token(TokenType.EOF, None, self.pos))
        return tokens

    def _peek(self) -> str | None:
        if self.pos + 1 < self.len:
            return self.text[self.pos + 1]
        return None

    def _peek_digit(self) -> bool:
        nxt = self._peek()
        return nxt is not None and nxt.isdigit()

    def _read_number(self) -> Token:
        start = self.pos
        has_dot = False
        while self.pos < self.len:
            ch = self.text[self.pos]
            if ch.isdigit():
                self.pos += 1
            elif ch == "." and not has_dot:
                has_dot = True
                self.pos += 1
            else:
                break
        num_str = self.text[start : self.pos]
        try:
            val = float(num_str) if has_dot else int(num_str)
            return Token(TokenType.NUMBER, val, start)
        except ValueError as err:
            raise FormulaError("#VALUE!", f"Invalid numeric literal '{num_str}'") from err

    def _read_string(self) -> Token:
        start = self.pos
        self.pos += 1  # Skip opening quote
        chars = []
        while self.pos < self.len:
            ch = self.text[self.pos]
            if ch == '"':
                if self._peek() == '"':
                    # Escaped quote: ""
                    chars.append('"')
                    self.pos += 2
                    continue
                else:
                    # Closing quote
                    self.pos += 1
                    return Token(TokenType.STRING, "".join(chars), start)
            else:
                chars.append(ch)
                self.pos += 1
        raise FormulaError("#VALUE!", "Unterminated string literal")

    def _read_identifier(self) -> Token:
        start = self.pos
        # Consume letters and digits
        while self.pos < self.len and (
            self.text[self.pos].isalnum() or self.text[self.pos] in "_$"
        ):
            self.pos += 1
        ident = self.text[start : self.pos].upper().replace("$", "")

        # Check for range: IDENT:IDENT (e.g. A1:B10)
        if self.pos < self.len and self.text[self.pos] == ":":
            self.pos += 1  # Skip colon
            range_start = self.pos
            while self.pos < self.len and (
                self.text[self.pos].isalnum() or self.text[self.pos] in "_$"
            ):
                self.pos += 1
            ident2 = self.text[range_start : self.pos].upper().replace("$", "")
            range_str = f"{ident}:{ident2}"
            try:
                cell_range = parse_range_ref(range_str)
                return Token(TokenType.RANGE_REF, cell_range, start)
            except Exception as err:
                raise FormulaError("#REF!", f"Invalid range reference '{range_str}'") from err

        # Check Boolean
        if ident == "TRUE":
            return Token(TokenType.BOOLEAN, True, start)
        if ident == "FALSE":
            return Token(TokenType.BOOLEAN, False, start)

        # Check Function call (followed by '(')
        if ident in SUPPORTED_FUNCTIONS:
            # Peak ahead past spaces to check if followed by '('
            idx = self.pos
            while idx < self.len and self.text[idx].isspace():
                idx += 1
            if idx < self.len and self.text[idx] == "(":
                return Token(TokenType.FUNCTION, ident, start)

        # Check Cell Reference
        try:
            coord = parse_cell_ref(ident)
            return Token(TokenType.CELL_REF, coord, start)
        except FormulaError:
            pass

        # If not a recognized function or cell ref
        if ident in SUPPORTED_FUNCTIONS:
            return Token(TokenType.FUNCTION, ident, start)

        raise FormulaError("#NAME?", f"Unknown function or name: '{ident}'")
