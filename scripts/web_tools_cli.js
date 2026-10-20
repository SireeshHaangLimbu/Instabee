// Run the browser tools in Node against web/data: node scripts/web_tools_cli.js '<json list of [tool, args]>'
const fs = require("fs");
const path = require("path");
const { createTools } = require("../web/tools.js");

const DATA = path.join(__dirname, "..", "web", "data");
const loader = {
  json: async (name) => JSON.parse(fs.readFileSync(path.join(DATA, name), "utf8")),
  binary: async (name) => { const b = Buffer.from(fs.readFileSync(path.join(DATA, name), "ascii"), "base64"); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); },
};

(async () => {
  const calls = JSON.parse(process.argv[2] || fs.readFileSync(0, "utf8"));
  const t = createTools(loader);
  const out = [];
  for (const [tool, args] of calls) {
    if (tool === "toSweref") { await t.init(); out.push(t.toSweref(args.lat, args.lon)); continue; }
    try { out.push(await t.tools[tool](args)); } catch (e) { out.push({ error: String(e.message || e) }); }
  }
  process.stdout.write(JSON.stringify(out));
})();
