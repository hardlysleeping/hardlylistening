// The app's CSS for fixtures that build their own page.
//
// Tests used to paste `index.html`'s inline <style> block and polish.css together. Both are
// gone: the app now loads /styles/app.css, which imports the component files into cascade
// layers. This helper resolves those imports back into one stylesheet, in the same order and
// the same layers, so a fixture renders exactly what the page renders.
const fs = require("fs"), path = require("path");

const STYLES = path.join(__dirname, "..", "web", "styles");
const seen = new Set();

function expand(file) {
  const abs = path.resolve(STYLES, file);
  if (seen.has(abs)) return "";
  seen.add(abs);
  const css = fs.readFileSync(abs, "utf8");
  const dir = path.dirname(abs);
  return css.replace(/@import\s+url\("([^"]+)"\)\s+layer\(([\w-]+)\);/g, (_, ref, layer) => {
    const inner = path.resolve(dir, ref);
    return `@layer ${layer} {\n${expand(path.relative(STYLES, inner))}\n}`;
  });
}

/** The whole app stylesheet, imports resolved. */
function appCss() {
  seen.clear();
  return expand("app.css");
}

/** That stylesheet as a <style> tag, for page.setContent(). */
function styleTag() {
  return `<style>${appCss()}</style>`;
}

module.exports = { appCss, styleTag };