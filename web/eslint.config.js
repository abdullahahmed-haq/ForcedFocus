module.exports = [
  {
    files: ["js/*.js", "shared/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        document: "readonly",
        window: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        setInterval: "readonly",
        clearTimeout: "readonly",
        clearInterval: "readonly",
        fetch: "readonly",
        AbortController: "readonly"
      }
    },
    rules: {
      "no-unused-vars": "error"
    }
  }
];
