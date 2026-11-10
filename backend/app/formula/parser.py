from __future__ import annotations

from app.formula.ast import (
    ASTNode,
    BinaryOpNode,
    CellRefNode,
    FunctionCallNode,
    LiteralNode,
    RangeRefNode,
    UnaryOpNode,
)
from app.formula.tokens import Token, Tokenizer, TokenType
from app.formula.types import FormulaError, format_cell_ref

MAX_AST_NODES = 250
MAX_NESTING_DEPTH = 25
MAX_RANGE_SIZE = 10000


class Parser:
    """Recursive descent formula parser with strict resource limits and validation."""

    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.depth = 0
        self.node_count = 0

    @classmethod
    def parse_formula(cls, formula_text: str) -> ASTNode:
        """Convenience entrypoint to tokenize and parse a formula string."""
        tokenizer = Tokenizer(formula_text)
        tokens = tokenizer.tokenize()
        parser = cls(tokens)
        ast = parser.parse()
        if ast.node_count() > MAX_AST_NODES:
            raise FormulaError(
                "#NUM!",
                f"Formula complexity exceeded: {ast.node_count()} AST nodes (limit: {MAX_AST_NODES})",
            )
        return ast

    def parse(self) -> ASTNode:
        node = self._parse_comparison()
        if not self._check(TokenType.EOF):
            tok = self._current()
            raise FormulaError("#VALUE!", f"Unexpected token '{tok.value}' at position {tok.pos}")
        return node

    def _enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_NESTING_DEPTH:
            raise FormulaError(
                "#NUM!", f"Maximum formula nesting depth exceeded (limit: {MAX_NESTING_DEPTH})"
            )

    def _exit(self) -> None:
        self.depth -= 1

    # --------------------------------------------------------------------------
    # Grammar Levels
    # --------------------------------------------------------------------------

    def _parse_comparison(self) -> ASTNode:
        """Parse comparison expressions: =, <>, <, <=, >, >="""
        left = self._parse_addition()
        while self._match(TokenType.OPERATOR, values={"=", "<>", "<", "<=", ">", ">="}):
            op = self._previous().value
            right = self._parse_addition()
            left = BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_addition(self) -> ASTNode:
        """Parse additive expressions: +, -"""
        left = self._parse_multiplication()
        while self._match(TokenType.OPERATOR, values={"+", "-"}):
            op = self._previous().value
            right = self._parse_multiplication()
            left = BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_multiplication(self) -> ASTNode:
        """Parse multiplicative expressions: *, /"""
        left = self._parse_exponentiation()
        while self._match(TokenType.OPERATOR, values={"*", "/"}):
            op = self._previous().value
            right = self._parse_exponentiation()
            left = BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_exponentiation(self) -> ASTNode:
        """Parse exponentiation expressions: ^ (right-associative)"""
        left = self._parse_unary()
        if self._match(TokenType.OPERATOR, values={"^"}):
            op = self._previous().value
            right = self._parse_exponentiation()  # right-associative recursion
            return BinaryOpNode(op=op, left=left, right=right)
        return left

    def _parse_unary(self) -> ASTNode:
        """Parse unary prefix operators: +, -"""
        if self._match(TokenType.OPERATOR, values={"+", "-"}):
            op = self._previous().value
            operand = self._parse_unary()
            return UnaryOpNode(op=op, operand=operand)
        return self._parse_primary()

    def _parse_primary(self) -> ASTNode:
        """Parse primary values: Literals, References, Function calls, Grouping"""
        self._enter()
        try:
            # Number literal
            if self._match(TokenType.NUMBER):
                return LiteralNode(value=self._previous().value, literal_type="number")

            # String literal
            if self._match(TokenType.STRING):
                return LiteralNode(value=self._previous().value, literal_type="string")

            # Boolean literal
            if self._match(TokenType.BOOLEAN):
                return LiteralNode(value=self._previous().value, literal_type="boolean")

            # Range reference (e.g. A1:B10)
            if self._match(TokenType.RANGE_REF):
                rng = self._previous().value
                if rng.count() > MAX_RANGE_SIZE:
                    raise FormulaError(
                        "#NUM!",
                        f"Range size of {rng.count()} cells exceeds limit of {MAX_RANGE_SIZE}",
                    )
                ref_str = f"{format_cell_ref(rng.cells()[0])}:{format_cell_ref(rng.cells()[-1])}"
                return RangeRefNode(cell_range=rng, ref=ref_str)

            # Single cell reference (e.g. A1)
            if self._match(TokenType.CELL_REF):
                coord = self._previous().value
                return CellRefNode(coord=coord, ref=format_cell_ref(coord))

            # Function call
            if self._match(TokenType.FUNCTION):
                fn_name = self._previous().value
                self._consume(TokenType.LPAREN, f"Expected '(' after function {fn_name}")
                args: list[ASTNode] = []
                if not self._check(TokenType.RPAREN):
                    while True:
                        args.append(self._parse_comparison())
                        if not self._match(TokenType.COMMA):
                            break
                self._consume(TokenType.RPAREN, f"Expected ')' closing function {fn_name}")
                return FunctionCallNode(name=fn_name, args=args)

            # Parenthesized group
            if self._match(TokenType.LPAREN):
                expr = self._parse_comparison()
                self._consume(TokenType.RPAREN, "Expected ')' closing parenthesized expression")
                return expr

            curr = self._current()
            raise FormulaError("#VALUE!", f"Unexpected token '{curr.value}' at position {curr.pos}")
        finally:
            self._exit()

    # --------------------------------------------------------------------------
    # Token Navigation Helpers
    # --------------------------------------------------------------------------

    def _match(self, token_type: TokenType, values: set[str] | None = None) -> bool:
        if self._check(token_type):
            if values is not None and self._current().value not in values:
                return False
            self._advance()
            return True
        return False

    def _check(self, token_type: TokenType) -> bool:
        if self._is_at_end():
            return token_type == TokenType.EOF
        return self._current().type == token_type

    def _advance(self) -> Token:
        if not self._is_at_end():
            self.pos += 1
        return self._previous()

    def _is_at_end(self) -> bool:
        return self._current().type == TokenType.EOF

    def _current(self) -> Token:
        return self.tokens[self.pos]

    def _previous(self) -> Token:
        return self.tokens[self.pos - 1]

    def _consume(self, token_type: TokenType, error_message: str) -> Token:
        if self._check(token_type):
            return self._advance()
        raise FormulaError("#VALUE!", error_message)
