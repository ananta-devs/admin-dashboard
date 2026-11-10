import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  colToLetter,
  letterToCol,
  formatCellRef,
  parseCellRef,
} from "@/lib/spreadsheet-types";
import { FormulaBar } from "@/components/spreadsheet/formula-bar";
import { SheetTabs } from "@/components/spreadsheet/sheet-tabs";
import SpreadsheetsPage from "@/app/spreadsheets/page";

describe("Spreadsheet Coordinate Helpers", () => {
  it("converts column indices to letters and vice versa", () => {
    expect(colToLetter(0)).toBe("A");
    expect(colToLetter(25)).toBe("Z");
    expect(colToLetter(26)).toBe("AA");
    expect(colToLetter(27)).toBe("AB");

    expect(letterToCol("A")).toBe(0);
    expect(letterToCol("Z")).toBe(25);
    expect(letterToCol("AA")).toBe(26);
    expect(letterToCol("AB")).toBe(27);
  });

  it("formats and parses cell references accurately", () => {
    expect(formatCellRef(0, 0)).toBe("A1");
    expect(formatCellRef(9, 2)).toBe("C10");
    expect(formatCellRef(49999, 25)).toBe("Z50000");

    expect(parseCellRef("A1")).toEqual({ row: 0, col: 0 });
    expect(parseCellRef("C10")).toEqual({ row: 9, col: 2 });
    expect(parseCellRef("Z50000")).toEqual({ row: 49999, col: 25 });
  });
});

describe("FormulaBar Component", () => {
  it("renders active cell address and formula value", () => {
    const onChange = vi.fn();
    const onCommit = vi.fn();
    const onCancel = vi.fn();
    const onSelectCell = vi.fn();

    render(
      <FormulaBar
        activeRow={4}
        activeCol={1}
        value="=SUM(B2:B4)"
        computedValue={45000}
        errorCode={null}
        onChange={onChange}
        onCommit={onCommit}
        onCancel={onCancel}
        onSelectCell={onSelectCell}
      />
    );

    const addressInput = screen.getByLabelText("Active cell address");
    expect(addressInput).toBeDefined();
    expect((addressInput as HTMLInputElement).value).toBe("B5");

    const formulaInput = screen.getByLabelText("Cell formula or value");
    expect((formulaInput as HTMLInputElement).value).toBe("=SUM(B2:B4)");
    expect(screen.getByText("45000")).toBeDefined();
  });

  it("displays typed formula errors", () => {
    render(
      <FormulaBar
        activeRow={0}
        activeCol={0}
        value="=A1/0"
        computedValue="#DIV/0!"
        errorCode="#DIV/0!"
        onChange={vi.fn()}
        onCommit={vi.fn()}
        onCancel={vi.fn()}
        onSelectCell={vi.fn()}
      />
    );

    expect(screen.getByText("#DIV/0!")).toBeDefined();
  });
});

describe("SheetTabs Component", () => {
  it("renders sheet tabs and handles switching and adding", () => {
    const onSelectSheet = vi.fn();
    const onAddSheet = vi.fn();
    const onRenameSheet = vi.fn();

    const mockSheets = [
      {
        id: "sheet-1",
        workbook_id: "wb-1",
        organization_id: "org-1",
        name: "Financials",
        position: 0,
        row_count: 50000,
        column_count: 26,
        version: 1,
        created_at: "",
        updated_at: "",
      },
      {
        id: "sheet-2",
        workbook_id: "wb-1",
        organization_id: "org-1",
        name: "Projections",
        position: 1,
        row_count: 50000,
        column_count: 26,
        version: 1,
        created_at: "",
        updated_at: "",
      },
    ];

    render(
      <SheetTabs
        sheets={mockSheets}
        activeSheetId="sheet-1"
        onSelectSheet={onSelectSheet}
        onAddSheet={onAddSheet}
        onRenameSheet={onRenameSheet}
      />
    );

    expect(screen.getByText("Financials")).toBeDefined();
    expect(screen.getByText("Projections")).toBeDefined();

    fireEvent.click(screen.getByText("Projections"));
    expect(onSelectSheet).toHaveBeenCalledWith("sheet-2");

    const addBtn = screen.getByLabelText("Add new sheet");
    fireEvent.click(addBtn);
    expect(onAddSheet).toHaveBeenCalled();
  });
});

describe("SpreadsheetsPage Full View", () => {
  it("renders page with 50,000 rows virtualized grid and clean empty workbook", async () => {
    render(<SpreadsheetsPage />);

    expect(await screen.findByText("Untitled Workbook")).toBeDefined();
    expect(screen.getByText("50,000 Rows")).toBeDefined();
    expect(screen.getByText("New Workbook")).toBeDefined();

    // Verify grid role and formula bar
    expect(screen.getByRole("grid")).toBeDefined();
    expect(screen.getByLabelText("Formula bar")).toBeDefined();

    // Verify sheet tab
    expect(screen.getByText("Sheet 1")).toBeDefined();
  });
});
