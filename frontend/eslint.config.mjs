import nextVitals from "eslint-config-next/core-web-vitals";
import { defineConfig, globalIgnores } from "eslint/config";

export default defineConfig([
  ...nextVitals,
  {
    // Components read DESIGN.md tokens; literal colours and px font sizes bypass them.
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        {
          selector: "Literal[value=/#[0-9a-fA-F]{3,8}\\b/], TemplateElement[value.raw=/#[0-9a-fA-F]{3,8}\\b/]",
          message: "Use a colour token from tokens.css, not a hex literal.",
        },
        {
          selector:
            "Property[key.name='fontSize'] > Literal[raw=/^[^0-9.]?[0-9.]+(px)?[^0-9.]?$/], Literal[value=/font-size:\\s*[0-9.]+px|text-\\[[0-9.]+px\\]/], TemplateElement[value.raw=/font-size:\\s*[0-9.]+px|text-\\[[0-9.]+px\\]/]",
          message: "Use a type-scale token from tokens.css, not a px font size.",
        },
      ],
    },
  },
  globalIgnores([".next/**", "next-env.d.ts", "playwright-report/**", "test-results/**"]),
]);
