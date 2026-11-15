// The page's JavaScript as one string.
//
// The inline script has been peeled into classic scripts a slice at a time (JS-030…032: repo
// discovery, Activity, the library's drawing layer), and they load before it. A test that
// slices a block out of "the page" should not have to know which file the block landed in:
// write it here once and pass the path when it moves again.
const fs = require("fs"), path = require("path");
const WEB = path.join(__dirname, "..", "web");

function indexHtml() {
  return fs.readFileSync(path.join(WEB, "index.html"), "utf8");
}

function inline() {
  const html = indexHtml();
  const at = html.indexOf("const $ = (s, r=document)");
  const end = html.indexOf("</script>", at);
  if (at < 0 || end <= at) throw new Error("the inline script is not where the page says it is");
  return html.slice(at, end);
}

function featureScripts() {
  const dir = path.join(WEB, "js");
  return fs.readdirSync(dir).filter(f => f.endsWith(".js")).sort()
    .map(f => fs.readFileSync(path.join(dir, f), "utf8"));
}

// Inline first, then the files it loads: an anchor that moved keeps its neighbours.
function js() {
  return [inline(), ...featureScripts()].join("\n");
}

// One file by name, for a block that is the tail of it and cannot be sliced by an anchor
// that still lives in index.html.
function feature(name) {
  return fs.readFileSync(path.join(WEB, "js", name), "utf8");
}

module.exports = { indexHtml, inline, js, feature };