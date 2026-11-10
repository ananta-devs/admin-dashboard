# SPREADSHEET ENGINE ARCHITECTURE & FORMULA SPECIFICATION
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Engine Model:** TanStack Table v9 + TanStack Virtual + Isolated DAG Calculation Core  
**Virtualization Capability:** Up to 100,000 cells (5,000 rows x 20 columns) in continuous DOM windowing

---

## 1. Engine Architecture & Component Separation

```
+-------------------------------------------------------------------------+
| Next.js Client / UI Layer                                              |
|                                                                         |
|  [ Virtualized Sheet Viewport ] (TanStack Virtual - DOM Row/Col Window)  |
|  [ Cell Matrix Selection Manager ] (Shift+Click, Drag Range, Focus)     |
|  [ Clipboard Controller ] (TSV Parse/Stringify for native Excel paste)  |
|  [ Inline Editor ] (Active Input with type-specific validation)         |
+-------------------------------------------------------------------------+
                                    |
                                    v (Dispatches Edit Action)
+-------------------------------------------------------------------------+
| Client-Side Calculation & State Core                                   |
|                                                                         |
|  [ Formula Lexer & Parser ] (String -> AST, zero eval)                  |
|  [ Dependency Graph (DAG) ] (Cell Reference Mapping & Cycle Detection)  |
|  [ Recalculation Scheduler ] (Topological Sort Evaluator)               |
|  [ Optimistic State Manager ] (Undo / Redo History Stack)               |
+-------------------------------------------------------------------------+
                                    |
                                    v (Debounced Patch Payload via HTTP)
+-------------------------------------------------------------------------+
| FastAPI Backend Persistence & Concurrency Guard                        |
|                                                                         |
|  [ Optimistic Lock Validator ] (Matches row `version`)                  |
|  [ Server-Side AST Evaluator ] (Validates computed values)              |
|  [ PostgreSQL Hybrid Storage ] (Atomic JSONB row patch)                 |
+-------------------------------------------------------------------------+
```

---

## 2. Formula Grammar & Lexical Specification

The formula engine operates with strict deterministic grammar:

### 2.1 Lexical Tokens
- **Identifiers:** Case-insensitive function names: `SUM`, `AVERAGE`, `COUNT`, `COUNTA`, `MIN`, `MAX`, `IF`, `AND`, `OR`, `NOT`, `CONCAT`, `LEN`, `TRIM`, `UPPER`, `LOWER`.
- **References:**
  - Single Cell: `[A-Z]+[0-9]+` (e.g., `A1`, `B12`, `$A$1`)
  - Range: `[A-Z]+[0-9]+:[A-Z]+[0-9]+` (e.g., `A1:B10`, `C3:C50`)
- **Literals:**
  - Numbers: `[0-9]+(\.[0-9]+)?` (e.g., `42`, `3.14159`)
  - Strings: `"([^"\\]|\\.)*"` (e.g., `"Completed"`, `"N/A"`)
  - Booleans: `TRUE`, `FALSE`
- **Operators:**
  - Arithmetic: `+` (Add), `-` (Subtract), `*` (Multiply), `/` (Divide), `^` (Exponentiate), `%` (Modulo)
  - Comparison: `=` (Equal), `<>` (Not Equal), `>` (Greater), `<` (Less), `>=` (Greater/Equal), `<=` (Less/Equal)
  - Grouping: `(`, `)`
  - Argument Delimiter: `,`

### 2.2 AST Node Structure (TypeScript & Python)
```typescript
export type ASTNode =
  | { type: 'Literal'; value: number | string | boolean }
  | { type: 'CellReference'; col: string; row: number; absoluteCol: boolean; absoluteRow: boolean }
  | { type: 'RangeReference'; start: CellReference; end: CellReference }
  | { type: 'BinaryOp'; operator: '+' | '-' | '*' | '/' | '^' | '=' | '<>' | '>' | '<' | '>=' | '<='; left: ASTNode; right: ASTNode }
  | { type: 'UnaryOp'; operator: '-' | '+'; operand: ASTNode }
  | { type: 'FunctionCall'; name: string; args: ASTNode[] };
```

---

## 3. Dependency Graph & Recalculation Engine

### 3.1 Directed Acyclic Graph (DAG)
Every formula is parsed to extract referenced dependencies:
- For cell `C1 = A1 + B1`, the graph registers directed edges: `A1 -> C1` and `B1 -> C1`.
- When `A1` is edited, a breadth-first search identifies all downstream dependents (`C1`, and any cells referencing `C1`).

### 3.2 Circular Reference Detection (Cycle Check)
Before adding dependencies to the graph, Tarjan's strongly connected components algorithm or a recursive cycle detector checks whether adding edge $U \to V$ introduces a path from $V \to U$:
- If a cycle is detected, evaluation halts immediately and the cell displays `#CYCLE!`.

### 3.3 Recalculation Scheduling
- Dependents are sorted topologically so that prerequisite cells are evaluated before the formulas consuming them.
- Recalculation executes in memory using cached values. If an evaluation encounters a division by zero, `#DIV/0!` is returned without crashing the calculation pipeline.

---

## 4. Undo, Redo & Optimistic Concurrency Control (OCC)

### 4.1 Client Undo / Redo
- The client maintains an immutable action stack (maximum 50 states).
- Actions represent reversible deltas:
  ```typescript
  interface CellDeltaAction {
    type: 'CELL_UPDATE';
    sheetId: string;
    rowId: string;
    colKey: string;
    oldValue: any;
    newValue: any;
    oldFormula?: string;
    newFormula?: string;
  }
  ```
- Pressing `Ctrl+Z` reverses the delta and triggers recalculation of downstream dependents.

### 4.2 Concurrent Editing & Conflict Resolution
- When saving changes, the client submits the current row `version` along with the cell modifications.
- FastAPI verifies:
  ```sql
  UPDATE sheet_records
  SET data = jsonb_set(data, '{col_key}', to_jsonb(:new_val)),
      version = version + 1,
      updated_at = NOW()
  WHERE id = :row_id AND version = :expected_version;
  ```
- If row version mismatch occurs (another user modified the row), the API returns `HTTP 409 Conflict` with the latest record payload. The client presents a side-by-side reconciliation prompt rather than silently overwriting.

