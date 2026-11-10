import type { ChartThemeTokens } from "./chart-types";

export const LIGHT_PALETTE = [
  "#635BFF", // Primary Indigo
  "#0EA5E9", // Sky Blue
  "#10B981", // Emerald Green
  "#F59E0B", // Amber
  "#EC4899", // Pink
  "#8B5CF6", // Purple
  "#14B8A6", // Teal
  "#F97316", // Orange
];

export const DARK_PALETTE = [
  "#8B83FF", // Primary Bright Indigo
  "#38BDF8", // Bright Sky
  "#34D399", // Mint Green
  "#FBBF24", // Golden Amber
  "#F472B6", // Rose Pink
  "#A78BFA", // Light Purple
  "#2DD4BF", // Bright Teal
  "#FB923C", // Bright Orange
];

export const LIGHT_THEME_TOKENS: ChartThemeTokens = {
  mode: "light",
  background: "transparent",
  surface: "#FFFFFF",
  foreground: "#151B2C",
  mutedForeground: "#687386",
  border: "#E7EAF0",
  primary: "#635BFF",
  palette: LIGHT_PALETTE,
  gridLineColor: "#E7EAF0",
  tooltipBackground: "rgba(255, 255, 255, 0.95)",
  tooltipBorder: "#E7EAF0",
  tooltipText: "#151B2C",
};

export const DARK_THEME_TOKENS: ChartThemeTokens = {
  mode: "dark",
  background: "transparent",
  surface: "#111827",
  foreground: "#F1F5F9",
  mutedForeground: "#94A3B8",
  border: "#1F293D",
  primary: "#8B83FF",
  palette: DARK_PALETTE,
  gridLineColor: "#1F293D",
  tooltipBackground: "rgba(17, 24, 39, 0.95)",
  tooltipBorder: "#1F293D",
  tooltipText: "#F1F5F9",
};

export function getChartThemeTokens(isDark: boolean): ChartThemeTokens {
  return isDark ? DARK_THEME_TOKENS : LIGHT_THEME_TOKENS;
}

