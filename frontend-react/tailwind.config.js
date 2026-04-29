const defaultTheme = require("tailwindcss/defaultTheme");

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx}",
    "./src/**/*.{js,ts,jsx,tsx}"
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Josefin Sans"', ...defaultTheme.fontFamily.sans]
      },
      colors: {
        theme: {
          primary: "#0069fe",
          light: "#f7f7f7",
          dark: "#151622",
          "dark-container": "#232734"
        },
        gradientL: "#00DAEF",
        gradientR: "#105EFF"
      },
      backgroundImage: {
        "gradient-light": "linear-gradient(180deg, #f8fbff 0%, #edf3ff 100%)",
        "gradient-dark": "linear-gradient(180deg, #191c2b 0%, #121522 100%)",
        "button-gradient": 'linear-gradient(to right, theme("colors.gradientL"), theme("colors.gradientR"))'
      }
    }
  },
  important: true,
  darkMode: "class",
  plugins: []
};
