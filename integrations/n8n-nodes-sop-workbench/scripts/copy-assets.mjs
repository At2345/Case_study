// Copy node icons (SVG) next to the compiled node files; tsc only emits JavaScript.
import { cpSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

for (const dir of ["nodes", "credentials"]) {
  const walk = (src) => {
    for (const name of readdirSync(src)) {
      const path = join(src, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (name.endsWith(".svg")) cpSync(path, join("dist", path));
    }
  };
  walk(dir);
}
