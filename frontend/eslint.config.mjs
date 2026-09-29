import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

export default defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    // Accessibility rules, as errors.
    //
    // eslint-plugin-jsx-a11y is a direct devDependency even though the rules
    // are not imported here: eslint-config-next registers the plugin, and
    // registering it again in a flat config is an error in ESLint 9. Pinning
    // it directly means these rules cannot silently stop resolving if next
    // ever drops the plugin from its own config.
    //
    // eslint-config-next already registers eslint-plugin-jsx-a11y, but only
    // six of its rules, all at "warn": the ARIA-correctness subset. Nothing
    // it ships would catch a button with no accessible name, a click
    // handler on a non-interactive element, or a keyboard user being unable
    // to reach something that responds to a click. Those are the failures
    // that actually make the app unusable rather than merely non-conformant,
    // so they are promoted to errors here.
    //
    // Deliberately not enabling the plugin's `recommended` preset wholesale:
    // it cannot be added as a flat config on top of next's (it tries to
    // re-register the plugin, which ESLint 9 rejects), and several of its
    // rules overlap with what the TypeScript and React configs already
    // catch. This list is the part that was missing.
    rules: {
      // A <label> must actually name a control. This is the direction that
      // works on this codebase.
      //
      // Its inverse, control-has-associated-label, is deliberately NOT
      // enabled. It reports 26 errors on this repo, every one of them a
      // false positive: all of them are
      //     <label><span>Username</span><input /></label>
      // which is the most accessible markup there is. Verified with a
      // minimal probe -- the rule fires on the wrapping-label-with-nested-
      // span form and stays silent on aria-label and on a named <button>,
      // so it is failing to read the span's text rather than finding a
      // real gap. Enabling it would mean 26 errors of noise teaching
      // everyone to ignore the a11y lint, which is worse than not having
      // it. The reverse rule below covers the same mistake from the
      // label's side, where it can actually see it.
      "jsx-a11y/label-has-associated-control": "error",
      // Click handlers need keyboard equivalents and must be focusable.
      "jsx-a11y/click-events-have-key-events": "error",
      "jsx-a11y/no-static-element-interactions": "error",
      "jsx-a11y/interactive-supports-focus": "error",
      "jsx-a11y/no-noninteractive-element-interactions": "error",
      "jsx-a11y/no-noninteractive-tabindex": "error",
      "jsx-a11y/anchor-is-valid": "error",
      "jsx-a11y/heading-has-content": "error",
      // Promote next's six warnings to errors so a11y cannot rot silently
      // in a build that only ever fails on errors.
      "jsx-a11y/alt-text": "error",
      "jsx-a11y/aria-props": "error",
      "jsx-a11y/aria-proptypes": "error",
      "jsx-a11y/aria-unsupported-elements": "error",
      "jsx-a11y/role-has-required-aria-props": "error",
      "jsx-a11y/role-supports-aria-props": "error",
    },
  },
  globalIgnores([
    ".next/**",
    "node_modules/**",
    "out/**",
    "build/**",
  ]),
]);
