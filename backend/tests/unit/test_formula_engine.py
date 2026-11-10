import pytest

from app.formula import (
    CYCLE_ERROR,
    DIV_ZERO_ERROR,
    NAME_ERROR,
    NUM_ERROR,
    REF_ERROR,
    VALUE_ERROR,
    CellCoord,
    DependencyGraph,
    Evaluator,
    FormulaError,
    Parser,
    col_to_letter,
    extract_precedents,
    format_cell_ref,
    letter_to_col,
    parse_cell_ref,
    parse_range_ref,
)


class TestCellCoordinates:
    def test_col_to_letter_and_back(self):
        assert col_to_letter(0) == "A"
        assert col_to_letter(25) == "Z"
        assert col_to_letter(26) == "AA"
        assert col_to_letter(27) == "AB"
        assert col_to_letter(51) == "AZ"
        assert col_to_letter(52) == "BA"

        assert letter_to_col("A") == 0
        assert letter_to_col("Z") == 25
        assert letter_to_col("AA") == 26
        assert letter_to_col("BA") == 52

    def test_parse_and_format_cell_ref(self):
        c = parse_cell_ref("A1")
        assert c.row == 0 and c.col == 0
        assert format_cell_ref(c) == "A1"

        c2 = parse_cell_ref("C15")
        assert c2.row == 14 and c2.col == 2
        assert format_cell_ref(c2) == "C15"

        c3 = parse_cell_ref("AA100")
        assert c3.row == 99 and c3.col == 26
        assert format_cell_ref(c3) == "AA100"

        with pytest.raises(FormulaError) as exc:
            parse_cell_ref("INVALID")
        assert exc.value.code == REF_ERROR

    def test_parse_range_ref(self):
        rng = parse_range_ref("A1:B3")
        assert rng.start_row == 0 and rng.end_row == 2
        assert rng.start_col == 0 and rng.end_col == 1
        assert rng.count() == 6
        cells = rng.cells()
        assert len(cells) == 6
        assert cells[0] == CellCoord(0, 0)
        assert cells[-1] == CellCoord(2, 1)


class TestFormulaParser:
    def test_arithmetic_precedence(self):
        # 2 + 3 * 4 should be 14, not 20
        ast = Parser.parse_formula("=2 + 3 * 4")
        evaluator = Evaluator(cell_getter=lambda c: None)
        res = evaluator.evaluate(ast)
        assert res.value == 14

    def test_parentheses(self):
        ast = Parser.parse_formula("=(2 + 3) * 4")
        evaluator = Evaluator(cell_getter=lambda c: None)
        res = evaluator.evaluate(ast)
        assert res.value == 20

    def test_exponentiation_right_associativity(self):
        # 2 ^ 3 ^ 2 = 2 ^ 9 = 512
        ast = Parser.parse_formula("=2 ^ 3 ^ 2")
        evaluator = Evaluator(cell_getter=lambda c: None)
        res = evaluator.evaluate(ast)
        assert res.value == 512

    def test_unary_minus(self):
        ast = Parser.parse_formula("=-5 + 10")
        evaluator = Evaluator(cell_getter=lambda c: None)
        res = evaluator.evaluate(ast)
        assert res.value == 5

    def test_comparison_operators(self):
        evaluator = Evaluator(cell_getter=lambda c: None)
        assert evaluator.evaluate(Parser.parse_formula("=5 > 3")).value is True
        assert evaluator.evaluate(Parser.parse_formula("=5 < 3")).value is False
        assert evaluator.evaluate(Parser.parse_formula("=5 >= 5")).value is True
        assert evaluator.evaluate(Parser.parse_formula("=4 <= 3")).value is False
        assert evaluator.evaluate(Parser.parse_formula("=10 = 10")).value is True
        assert evaluator.evaluate(Parser.parse_formula("=10 <> 10")).value is False
        assert evaluator.evaluate(Parser.parse_formula('="apple" = "APPLE"')).value is True

    def test_string_literal(self):
        ast = Parser.parse_formula('="Hello, " + "World!"')
        # In spreadsheet arithmetic, string + string causes #VALUE!
        evaluator = Evaluator(cell_getter=lambda c: None)
        res = evaluator.evaluate(ast)
        assert res.error_code == VALUE_ERROR

        ast2 = Parser.parse_formula('="Hello"')
        assert evaluator.evaluate(ast2).value == "Hello"

    def test_parser_limits(self):
        # Formula length limit
        long_formula = "=1" + ("+1" * 600)
        with pytest.raises(FormulaError) as exc:
            Parser.parse_formula(long_formula)
        assert exc.value.code == NUM_ERROR

        # Nesting depth limit
        deep_formula = ("(" * 30) + "1" + (")" * 30)
        with pytest.raises(FormulaError) as exc:
            Parser.parse_formula(deep_formula)
        assert exc.value.code == NUM_ERROR


class TestFormulaEvaluator:
    def test_cell_references_and_aggregates(self):
        # Grid setup:
        # A1: 10, A2: 20, A3: 30
        grid = {
            (0, 0): 10,
            (1, 0): 20,
            (2, 0): 30,
        }

        def getter(coord: CellCoord):
            return grid.get((coord.row, coord.col))

        evaluator = Evaluator(cell_getter=getter)

        # Single reference
        assert evaluator.evaluate(Parser.parse_formula("=A1 + A2")).value == 30
        assert evaluator.evaluate(Parser.parse_formula("=A3 - A1")).value == 20

        # SUM
        assert evaluator.evaluate(Parser.parse_formula("=SUM(A1:A3)")).value == 60
        assert evaluator.evaluate(Parser.parse_formula("=SUM(A1, A2, 5)")).value == 35

        # AVERAGE
        assert evaluator.evaluate(Parser.parse_formula("=AVERAGE(A1:A3)")).value == 20

        # COUNT
        assert evaluator.evaluate(Parser.parse_formula("=COUNT(A1:A3)")).value == 3

        # MIN / MAX
        assert evaluator.evaluate(Parser.parse_formula("=MIN(A1:A3)")).value == 10
        assert evaluator.evaluate(Parser.parse_formula("=MAX(A1:A3)")).value == 30

        # IF
        assert evaluator.evaluate(Parser.parse_formula('=IF(A1 > 5, "YES", "NO")')).value == "YES"
        assert evaluator.evaluate(Parser.parse_formula('=IF(A1 > 15, "YES", "NO")')).value == "NO"

    def test_formula_errors(self):
        evaluator = Evaluator(cell_getter=lambda c: None)

        # Division by zero
        res = evaluator.evaluate(Parser.parse_formula("=10 / 0"))
        assert res.error_code == DIV_ZERO_ERROR

        # Value error on invalid coercion
        res2 = evaluator.evaluate(Parser.parse_formula('="text" + 5'))
        assert res2.error_code == VALUE_ERROR

        # Unknown function
        with pytest.raises(FormulaError) as exc:
            Parser.parse_formula("=FOOBAR(1, 2)")
        assert exc.value.code == NAME_ERROR

        # Error propagation
        def error_getter(coord: CellCoord):
            if coord.row == 0 and coord.col == 0:
                return DIV_ZERO_ERROR
            return 5

        evaluator_err = Evaluator(cell_getter=error_getter)
        res_prop = evaluator_err.evaluate(Parser.parse_formula("=A1 + 10"))
        assert res_prop.error_code == DIV_ZERO_ERROR


class TestDependencyGraph:
    def test_precedent_extraction(self):
        ast = Parser.parse_formula("=A1 + B2 * SUM(C3:D4)")
        precedents = extract_precedents(ast)
        assert "A1" in precedents
        assert "B2" in precedents
        assert "C3" in precedents
        assert "C4" in precedents
        assert "D3" in precedents
        assert "D4" in precedents

    def test_topological_recalculation_order(self):
        graph = DependencyGraph()
        # B1 = A1 * 2
        graph.set_dependencies("B1", {"A1"})
        # C1 = B1 + 10
        graph.set_dependencies("C1", {"B1"})
        # D1 = A1 + C1
        graph.set_dependencies("D1", {"A1", "C1"})

        order = graph.get_recalculation_order(["A1"])
        assert order == ["B1", "C1", "D1"]

    def test_circular_dependency_detection(self):
        graph = DependencyGraph()
        # A1 depends on B1, B1 depends on A1
        graph.set_dependencies("A1", {"B1"})
        graph.set_dependencies("B1", {"A1"})

        with pytest.raises(FormulaError) as exc:
            graph.get_recalculation_order(["A1"])
        assert exc.value.code == CYCLE_ERROR

    def test_three_node_cycle(self):
        graph = DependencyGraph()
        graph.set_dependencies("A1", {"C1"})
        graph.set_dependencies("B1", {"A1"})
        graph.set_dependencies("C1", {"B1"})

        with pytest.raises(FormulaError) as exc:
            graph.get_recalculation_order(["A1"])
        assert exc.value.code == CYCLE_ERROR
