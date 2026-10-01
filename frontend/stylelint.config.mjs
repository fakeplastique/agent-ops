// Same contract as the eslint rule: literals live in tokens.css, components read variables.
const config = {
  ignoreFiles: ["src/styles/tokens.css"],
  rules: {
    "color-no-hex": true,
    // Any px length except the 1px hairline.
    "declaration-property-value-disallowed-list": {
      "/.*/": ["/(?<![\\d.])(?!1px\\b)[0-9]*\\.?[0-9]+px\\b/"],
    },
  },
};

export default config;
