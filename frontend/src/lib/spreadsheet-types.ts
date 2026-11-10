export interface CellData {
  raw: string | number | boolean | null;
  computed: string | number | boolean | null;
  formula: string | null;
  type: string;
  error_code: string | null;
}

export interface CellCoord {
  row: number; // 0-indexed
  col: number; // 0-indexed
}

export interface SheetRangeRead {
  sheet_id: string;
  start_row: number;
  end_row: number;
  start_col: number;
  end_col: number;
  cells: Record<string, CellData>;
  row_versions: Record<number, number>;
}

export interface SheetRead {
  id: string;
  workbook_id: string;
  organization_id: string;
  name: string;
  position: number;
  row_count: number;
  column_count: number;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface WorkbookRead {
  id: string;
  workspace_id: string;
  organization_id: string;
  name: string;
  description?: string | null;
  version: number;
  sheet_count?: number;
  sheets?: SheetRead[];
  created_at: string;
  updated_at: string;
}

export interface CellEditInput {
  cell_ref: string;
  value: string | number | boolean | null;
  expected_row_version?: number | null;
}

export interface BatchCellUpdateResponse {
  sheet_id: string;
  updated_cells: Record<string, CellData>;
  recalculated_count: number;
  row_versions: Record<number, number>;
}

/** Convert 0-indexed column integer to spreadsheet letters (0 -> A, 25 -> Z, 26 -> AA) */
export function colToLetter(col: number): string {
  if (col < 0) return "A";
  let result = "";
  let c = col;
  while (c >= 0) {
    result = String.fromCharCode(65 + (c % 26)) + result;
    c = Math.floor(c / 26) - 1;
  }
  return result;
}

/** Convert spreadsheet letters to 0-indexed column integer (A -> 0, Z -> 25, AA -> 26) */
export function letterToCol(letters: string): number {
  let col = 0;
  const upper = letters.toUpperCase();
  for (let i = 0; i < upper.length; i++) {
    const code = upper.charCodeAt(i);
    if (code < 65 || code > 90) continue;
    col = col * 26 + (code - 64);
  }
  return col - 1;
}

/** Format coordinate to cell reference string (e.g. {row: 0, col: 0} -> "A1") */
export function formatCellRef(row: number, col: number): string {
  return `${colToLetter(col)}${row + 1}`;
}

/** Parse cell reference string (e.g. "B5") to coordinate {row: 4, col: 1} */
export function parseCellRef(ref: string): CellCoord {
  const match = ref.trim().toUpperCase().match(/^([A-Z]+)([0-9]+)$/);
  if (!match) {
    return { row: 0, col: 0 };
  }
  const col = letterToCol(match[1]);
  const row = parseInt(match[2], 10) - 1;
  return { row: Math.max(0, row), col: Math.max(0, col) };
}

