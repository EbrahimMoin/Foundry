/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        obsidian: "#0b0f17",
        panel: "#111827",
        panelBorder: "#1f2937",
      },
    },
  },
  plugins: [],
};
