import { useCallback, useEffect, useState } from "react";

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "foundry-color-scheme";

function readSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStoredPreference(): ThemePreference {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // Ignore storage access restrictions
  }
  return "system";
}

function applyThemeToDocument(preference: ThemePreference, resolved: ResolvedTheme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", resolved === "dark");

  const meta = document.querySelector('meta[name="color-scheme"]');
  if (meta) {
    meta.setAttribute("content", preference === "system" ? "light dark" : preference);
  }
}

export function useTheme() {
  const [preference, setPreferenceState] = useState<ThemePreference>(readStoredPreference);
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(readSystemTheme);

  const resolvedTheme: ResolvedTheme =
    preference === "system" ? systemTheme : preference;

  useEffect(() => {
    applyThemeToDocument(preference, resolvedTheme);
  }, [preference, resolvedTheme]);

  // Track OS color scheme transitions without clobbering any stored user override.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (event: MediaQueryListEvent) => {
      setSystemTheme(event.matches ? "dark" : "light");
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  // Stay in sync when another tab updates the override.
  useEffect(() => {
    const onStorage = (event: StorageEvent) => {
      if (event.key !== STORAGE_KEY) return;
      setPreferenceState(
        event.newValue === "light" || event.newValue === "dark"
          ? event.newValue
          : "system"
      );
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      if (next === "system") {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, next);
      }
    } catch {
      // Theme still applies in-memory if storage is blocked
    }
  }, []);

  // Two-state chrome toggle per modern-web-guidance: revert to system when
  // toggling back to match the OS theme, rather than pinning it unnecessarily.
  const toggleTheme = useCallback(() => {
    const target: ResolvedTheme = resolvedTheme === "dark" ? "light" : "dark";
    if (target === systemTheme) {
      setPreference("system");
    } else {
      setPreference(target);
    }
  }, [resolvedTheme, systemTheme, setPreference]);

  return {
    preference,
    systemTheme,
    theme: resolvedTheme,
    setPreference,
    toggleTheme,
  };
}
