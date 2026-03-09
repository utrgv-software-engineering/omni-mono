import { createContext, useContext } from "react";
import { tokyoNight, type Theme, type ThemeName } from "./theme.js";

export type ThemeContextValue = {
  theme: Theme;
  themeName: ThemeName;
  setThemeName: (name: ThemeName) => void;
};

export const ThemeContext = createContext<ThemeContextValue>({
  theme: tokyoNight,
  themeName: "tokyo-night",
  setThemeName: () => {},
});

export function useTheme(): Theme {
  return useContext(ThemeContext).theme;
}

export function useThemeControl() {
  return useContext(ThemeContext);
}
