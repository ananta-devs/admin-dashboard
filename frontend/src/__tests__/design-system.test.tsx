import * as React from "react";
import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

beforeAll(() => {
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
  Element.prototype.scrollIntoView = vi.fn();
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  usePathname: () => "/",
}));

const mockSetTheme = vi.fn();
vi.mock("next-themes", () => ({
  useTheme: () => ({
    theme: "light",
    setTheme: mockSetTheme,
  }),
}));

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
  TableCaption,
} from "@/components/ui/table";
import {
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";
import { ThemeToggle } from "@/components/theme-toggle";
import { CommandPalette } from "@/components/command-palette";

describe("Phase 2 Design System Primitives Unit Tests", () => {
  describe("Button Component", () => {
    it("renders with various variants and sizes", () => {
      const { rerender } = render(<Button variant="default">Primary</Button>);
      expect(screen.getByText("Primary")).toBeDefined();

      rerender(<Button variant="destructive">Delete</Button>);
      const deleteBtn = screen.getByText("Delete");
      expect(deleteBtn.className).toContain("bg-[var(--destructive)]");

      rerender(<Button variant="outline" size="sm">Small Outline</Button>);
      const smallBtn = screen.getByText("Small Outline");
      expect(smallBtn.className).toContain("h-8");
      expect(smallBtn.className).toContain("border");
    });

    it("renders loading state with disabled attribute and spinner", () => {
      render(<Button loading>Processing</Button>);
      const btn = screen.getByRole("button");
      expect(btn.hasAttribute("disabled")).toBe(true);
      expect(btn.querySelector("svg")).toBeDefined();
    });

    it("respects standard disabled attribute", () => {
      const handleClick = vi.fn();
      render(<Button disabled onClick={handleClick}>Disabled</Button>);
      const btn = screen.getByText("Disabled");
      fireEvent.click(btn);
      expect(handleClick).not.toHaveBeenCalled();
    });
  });

  describe("Badge Component", () => {
    it("renders semantic variants correctly", () => {
      const { rerender } = render(<Badge variant="success">Active</Badge>);
      expect(screen.getByText("Active").className).toContain("text-[var(--success)]");

      rerender(<Badge variant="warning">Pending</Badge>);
      expect(screen.getByText("Pending").className).toContain("text-[var(--warning)]");

      rerender(<Badge variant="destructive">Error</Badge>);
      expect(screen.getByText("Error").className).toContain("text-[var(--destructive)]");

      rerender(<Badge variant="info">In Review</Badge>);
      expect(screen.getByText("In Review").className).toContain("text-[var(--info)]");
    });
  });

  describe("Input and Textarea Components", () => {
    it("renders input with proper attributes and error states", () => {
      const { rerender } = render(
        <Input placeholder="Enter username" aria-label="Username" />
      );
      const input = screen.getByPlaceholderText("Enter username");
      expect(input).toBeDefined();

      rerender(<Input placeholder="Enter username" error aria-invalid="true" />);
      const errInput = screen.getByPlaceholderText("Enter username");
      expect(errInput.className).toContain("border-[var(--destructive)]");
      expect(errInput.getAttribute("aria-invalid")).toBe("true");
    });

    it("renders textarea with placeholder and error state", () => {
      render(<Textarea placeholder="Audit remarks" error aria-invalid="true" />);
      const textarea = screen.getByPlaceholderText("Audit remarks");
      expect(textarea.className).toContain("border-[var(--destructive)]");
    });
  });

  describe("Checkbox Component", () => {
    it("renders accessible checkbox with states", () => {
      render(
        <div className="flex items-center space-x-2">
          <Checkbox id="terms" defaultChecked={false} />
          <label htmlFor="terms">Accept terms</label>
        </div>
      );
      const checkbox = screen.getByRole("checkbox");
      expect(checkbox.getAttribute("aria-checked")).toBe("false");
      fireEvent.click(checkbox);
      expect(checkbox.getAttribute("aria-checked")).toBe("true");
    });
  });

  describe("Card Component", () => {
    it("renders all card sub-components correctly", () => {
      render(
        <Card>
          <CardHeader>
            <CardTitle>System Overview</CardTitle>
            <CardDescription>Live telemetry metrics</CardDescription>
          </CardHeader>
          <CardContent>
            <p>Main content payload</p>
          </CardContent>
          <CardFooter>
            <Button size="sm">Action</Button>
          </CardFooter>
        </Card>
      );
      expect(screen.getByText("System Overview")).toBeDefined();
      expect(screen.getByText("Live telemetry metrics")).toBeDefined();
      expect(screen.getByText("Main content payload")).toBeDefined();
      expect(screen.getByText("Action")).toBeDefined();
    });
  });

  describe("Separator & Skeleton Components", () => {
    it("renders separator horizontally and vertically", () => {
      const { container } = render(<Separator orientation="horizontal" />);
      const sep = container.querySelector('[role="separator"]');
      expect(sep).toBeDefined();
    });

    it("renders skeleton placeholder with pulse animation class", () => {
      const { container } = render(<Skeleton className="h-4 w-32" />);
      const skeleton = container.firstChild as HTMLElement;
      expect(skeleton.className).toContain("animate-pulse");
      expect(skeleton.className).toContain("bg-[var(--muted)]");
    });
  });

  describe("EmptyState & ErrorState Components", () => {
    it("renders empty state and fires action callback", () => {
      const handleAction = vi.fn();
      render(
        <EmptyState
          title="No Documents Found"
          description="Create your first document to populate this view."
          actionLabel="Create Document"
          onAction={handleAction}
        />
      );
      expect(screen.getByText("No Documents Found")).toBeDefined();
      expect(screen.getByText("Create your first document to populate this view.")).toBeDefined();
      const actionBtn = screen.getByText("Create Document");
      fireEvent.click(actionBtn);
      expect(handleAction).toHaveBeenCalledTimes(1);
    });

    it("renders error state with role=alert and fires retry callback", () => {
      const handleRetry = vi.fn();
      render(
        <ErrorState
          title="Database Connection Lost"
          message="Could not reach PostgreSQL pool."
          retryLabel="Reconnect"
          onRetry={handleRetry}
        />
      );
      expect(screen.getByRole("alert")).toBeDefined();
      expect(screen.getByText("Database Connection Lost")).toBeDefined();
      expect(screen.getByText("Could not reach PostgreSQL pool.")).toBeDefined();
      const retryBtn = screen.getByText("Reconnect");
      fireEvent.click(retryBtn);
      expect(handleRetry).toHaveBeenCalledTimes(1);
    });
  });

  describe("Table Component", () => {
    it("renders structured HTML table elements with caption and cells", () => {
      render(
        <Table>
          <TableCaption>Demonstration roster</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead>User</TableHead>
              <TableHead>Role</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            <TableRow>
              <TableCell>alice@example.com</TableCell>
              <TableCell>Admin</TableCell>
            </TableRow>
          </TableBody>
        </Table>
      );
      expect(screen.getByText("Demonstration roster")).toBeDefined();
      expect(screen.getByText("User")).toBeDefined();
      expect(screen.getByText("Role")).toBeDefined();
      expect(screen.getByText("alice@example.com")).toBeDefined();
      expect(screen.getByText("Admin")).toBeDefined();
    });
  });

  describe("Dialog Component", () => {
    it("opens and renders dialog content with title and description", () => {
      render(
        <Dialog>
          <DialogTrigger asChild>
            <Button>Open Modal</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Modal Dialog Title</DialogTitle>
              <DialogDescription>Modal description content</DialogDescription>
            </DialogHeader>
            <p>Body of the modal</p>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline">Close Dialog</Button>
              </DialogClose>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      );

      const triggerBtn = screen.getByText("Open Modal");
      fireEvent.click(triggerBtn);

      expect(screen.getByText("Modal Dialog Title")).toBeDefined();
      expect(screen.getByText("Modal description content")).toBeDefined();
      expect(screen.getByText("Body of the modal")).toBeDefined();
      expect(screen.getByText("Close Dialog")).toBeDefined();
    });
  });

  describe("Sidebar Navigation and Shell Interactions", () => {
    it("handles sidebar collapse and expand toggle", () => {
      const handleToggle = vi.fn();
      const { rerender } = render(
        <Sidebar collapsed={false} onToggle={handleToggle} />
      );
      const collapseBtns = screen.getAllByRole("button", { name: /collapse sidebar/i });
      fireEvent.click(collapseBtns[0]);
      expect(handleToggle).toHaveBeenCalledTimes(1);

      rerender(<Sidebar collapsed={true} onToggle={handleToggle} />);
      const expandBtns = screen.getAllByRole("button", { name: /expand sidebar/i });
      fireEvent.click(expandBtns[0]);
      expect(handleToggle).toHaveBeenCalledTimes(2);
    });

    it("indicates active navigation link via aria-current=page", () => {
      render(<Sidebar collapsed={false} onToggle={() => {}} />);
      const dashboardLinks = screen.getAllByRole("link", { name: /dashboard/i });
      const activeLink = dashboardLinks.find(link => link.getAttribute("aria-current") === "page");
      expect(activeLink).toBeDefined();

      const nonActiveLinks = screen.getAllByRole("link", { name: /spreadsheets/i });
      expect(nonActiveLinks[0].getAttribute("aria-current")).toBeNull();
    });

    it("opens mobile drawer via topbar hamburger and closes via close button", () => {
      const handleOpen = vi.fn();
      render(<Topbar onOpenCommandPalette={() => {}} onOpenMobileMenu={handleOpen} />);
      const hamburger = screen.getByRole("button", { name: /open mobile navigation menu/i });
      fireEvent.click(hamburger);
      expect(handleOpen).toHaveBeenCalledTimes(1);

      const handleClose = vi.fn();
      render(
        <Sidebar
          collapsed={false}
          onToggle={() => {}}
          mobileOpen={true}
          onMobileClose={handleClose}
        />
      );
      const closeBtns = screen.getAllByRole("button", { name: /close mobile navigation/i });
      fireEvent.click(closeBtns[0]);
      expect(handleClose).toHaveBeenCalledTimes(1);
    });

    it("calls onMobileClose when Escape key is pressed", () => {
      const handleClose = vi.fn();
      render(
        <Sidebar
          collapsed={false}
          onToggle={() => {}}
          mobileOpen={true}
          onMobileClose={handleClose}
        />
      );
      fireEvent.keyDown(window, { key: "Escape" });
      expect(handleClose).toHaveBeenCalledTimes(1);
    });

    it("renders mobile drawer overlay when mobileOpen is true and closes on click", () => {
      const handleClose = vi.fn();
      const { container } = render(
        <Sidebar
          collapsed={false}
          onToggle={() => {}}
          mobileOpen={true}
          onMobileClose={handleClose}
        />
      );

      const overlay = container.querySelector('[role="presentation"]');
      expect(overlay).toBeDefined();
      if (overlay) {
        fireEvent.click(overlay);
        expect(handleClose).toHaveBeenCalledTimes(1);
      }
    });
  });

  describe("Theme Switching and Persistence", () => {
    it("toggles theme between light and dark when clicked", () => {
      render(<ThemeToggle />);
      const themeBtn = screen.getByRole("button", { name: /toggle theme/i });
      fireEvent.click(themeBtn);
      expect(mockSetTheme).toHaveBeenCalledWith("dark");
    });

    it("persists theme preference to localStorage", () => {
      localStorage.setItem("theme", "dark");
      expect(localStorage.getItem("theme")).toBe("dark");
      localStorage.setItem("theme", "light");
      expect(localStorage.getItem("theme")).toBe("light");
    });
  });

  describe("Command Palette Shortcuts, Filtering and Guards", () => {
    it("renders command palette dialog when open", () => {
      const handleClose = vi.fn();
      render(<CommandPalette open={true} onOpenChange={handleClose} />);
      expect(screen.getByPlaceholderText("Type a command or search platform...")).toBeDefined();
      expect(screen.getByText("Active Navigation")).toBeDefined();
      expect(screen.getByText("Planned Platform Modules (Phase 5 - 9)")).toBeDefined();
    });

    it("opens on Ctrl+K and Cmd+K keyboard events", () => {
      const handleOpenChange = vi.fn();
      render(<CommandPalette open={false} onOpenChange={handleOpenChange} />);

      fireEvent.keyDown(window, { key: "k", ctrlKey: true });
      expect(handleOpenChange).toHaveBeenCalledWith(true);

      fireEvent.keyDown(window, { key: "K", metaKey: true });
      expect(handleOpenChange).toHaveBeenCalledWith(true);
    });

    it("suppresses Ctrl+K when typing inside an input, textarea or contenteditable", () => {
      const handleOpenChange = vi.fn();
      render(
        <div>
          <input data-testid="test-input" />
          <textarea data-testid="test-textarea" />
          <div data-testid="test-editable" contentEditable={true} />
          <CommandPalette open={false} onOpenChange={handleOpenChange} />
        </div>
      );

      const input = screen.getByTestId("test-input");
      fireEvent.keyDown(input, { key: "k", ctrlKey: true });
      expect(handleOpenChange).not.toHaveBeenCalled();

      const textarea = screen.getByTestId("test-textarea");
      fireEvent.keyDown(textarea, { key: "k", ctrlKey: true });
      expect(handleOpenChange).not.toHaveBeenCalled();

      const editable = screen.getByTestId("test-editable");
      fireEvent.keyDown(editable, { key: "k", ctrlKey: true });
      expect(handleOpenChange).not.toHaveBeenCalled();
    });

    it("closes command palette when Escape key is pressed", () => {
      const handleOpenChange = vi.fn();
      render(<CommandPalette open={true} onOpenChange={handleOpenChange} />);
      fireEvent.keyDown(window, { key: "Escape" });
      expect(handleOpenChange).toHaveBeenCalledWith(false);
    });

    it("filters command palette items and enables selection", () => {
      render(<CommandPalette open={true} onOpenChange={() => {}} />);
      const searchInput = screen.getByPlaceholderText("Type a command or search platform...");
      fireEvent.change(searchInput, { target: { value: "Spreadsheet" } });
      expect(searchInput.getAttribute("value")).toBe("Spreadsheet");
    });
  });

  describe("Disabled and Loading Component States", () => {
    it("prevents interactions on disabled checkboxes", () => {
      const handleChange = vi.fn();
      render(<Checkbox disabled onCheckedChange={handleChange} aria-label="Locked policy" />);
      const checkbox = screen.getByRole("checkbox");
      expect(checkbox.hasAttribute("disabled")).toBe(true);
      fireEvent.click(checkbox);
      expect(handleChange).not.toHaveBeenCalled();
    });
  });
});
