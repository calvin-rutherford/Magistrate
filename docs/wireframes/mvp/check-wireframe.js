const fs = require("fs");
const path = require("path");

const root = __dirname;
const entry = path.join(root, "index.html");
const html = fs.readFileSync(entry, "utf8");
const failures = [];

for (const file of ["styles.css", "wireframe.js"]) {
  if (!fs.existsSync(path.join(root, file))) failures.push(`Missing ${file}`);
}

const idMatches = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
const ids = new Set(idMatches);
const hrefMatches = [...html.matchAll(/href="#([^"]+)"/g)].map((match) => match[1]);
for (const target of hrefMatches) {
  if (!ids.has(target)) failures.push(`Missing hash target #${target}`);
}

const assetMatches = [
  ...html.matchAll(/(?:src|href)="([^"]+\.(?:png|svg|css|js|webmanifest))"/g),
  ...fs.readFileSync(path.join(root, "styles.css"), "utf8").matchAll(/url\("([^"]+)"\)/g),
  ...fs.readFileSync(path.join(root, "styles.css"), "utf8").matchAll(/@import url\("([^"]+)"\)/g),
].map((match) => match[1]).filter((ref) => !ref.startsWith("http"));

for (const ref of assetMatches) {
  const resolved = path.join(root, ref);
  if (!fs.existsSync(resolved)) failures.push(`Missing asset ${ref}`);
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}

console.log(`Wireframe integrity OK: ${hrefMatches.length} links, ${assetMatches.length} assets.`);
