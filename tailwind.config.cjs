module.exports = {
  content: [
    './templates/**/*.html',
    './**/*.py'
  ],
  theme: {
    extend: {},
  },
  plugins: [
    require('daisyui'),
  ],
  darkMode: 'class',
  daisyui: {
    themes: ["black", "light"],
    darkTheme: "black",
  },
}
