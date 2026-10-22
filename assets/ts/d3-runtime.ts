// Bundled d3 draws without a module loader or network; caller networking is optional.
import * as d3 from "d3";

(globalThis as unknown as { d3: typeof d3 }).d3 = d3;
