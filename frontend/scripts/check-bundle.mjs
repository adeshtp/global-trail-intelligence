// Fails when any built browser chunk has a syntax error.
//
// A chunk that does not even parse stops everything in it from running, and
// nothing else in the build notices. This is how the production build once
// shipped a Cesium module the browser refused to evaluate ("Octal escape
// sequences are not allowed in template strings"), which left the map blank.
//
// Usage (after `npm run build`):  npm run check:bundle
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import vm from "node:vm";

const root = join(process.cwd(), ".next", "static", "chunks");

function scripts(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return scripts(path);
    return name.endsWith(".js") ? [path] : [];
  });
}

let files;
try {
  files = scripts(root);
} catch {
  console.error(`No build found at ${root}. Run \`npm run build\` first.`);
  process.exit(2);
}

const failures = [];
for (const file of files) {
  try {
    new vm.Script(readFileSync(file, "utf8"), { filename: file });
  } catch (error) {
    if (error instanceof SyntaxError) {
      failures.push(`${file.replace(process.cwd() + "/", "")}: ${error.message}`);
    } else {
      throw error;
    }
  }
}

if (failures.length > 0) {
  console.error(`${failures.length} chunk(s) do not parse:\n  ` + failures.join("\n  "));
  process.exit(1);
}
console.log(`All ${files.length} browser chunks parse.`);
