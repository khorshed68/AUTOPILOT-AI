import animate from "tailwindcss-animate";

const token = (name) => `hsl(var(--${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: { sans: ['"Inter Variable"', "ui-sans-serif", "system-ui", "sans-serif"] },
      fontSize: { "2xs": ["0.6875rem", "1rem"] },
      colors: {
        background: token("background"),
        surface: token("surface"),
        foreground: token("foreground"),
        muted: { DEFAULT: token("muted"), foreground: token("muted-foreground") },
        border: token("border"),
        input: token("input"),
        ring: token("ring"),
        primary: { DEFAULT: token("primary"), foreground: token("primary-foreground") },
        accent: { DEFAULT: token("accent"), foreground: token("foreground") },
        popover: { DEFAULT: token("popover"), foreground: token("foreground") },
        success: token("success"),
        warning: token("warning"),
        danger: token("danger"),
      },
      borderRadius: { lg: "0.625rem", md: "0.5rem", sm: "0.375rem" },
      boxShadow: { card: "0 1px 2px 0 rgb(0 0 0 / 0.04)", pop: "0 8px 28px -6px rgb(0 0 0 / 0.22)" },
    },
  },
  plugins: [animate],
};
