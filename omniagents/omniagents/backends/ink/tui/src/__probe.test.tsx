import React from "react";
import { PassThrough } from "node:stream";
import { it } from "vitest";
import { render, Box, Text } from "ink";

class Out extends PassThrough {
  frames: string[] = [];
  isTTY = true; columns = 24; rows = 10;
  constructor() { super(); this.setEncoding("utf8");
    this.on("data", (c) => typeof c === "string" && this.frames.push(c)); }
  grab(): string {
    for (let i = this.frames.length - 1; i >= 0; i--)
      if ((this.frames[i] ?? "").includes("beta")) return this.frames[i]!;
    return ""; }
}
const flush = () => new Promise((r) => setTimeout(r, 20));

it("what survives without colour", async () => {
  const out = new Out();
  const inst = render(
    <Box flexDirection="column" width={20}>
      <Box backgroundColor="#24283b"><Text>bg-band beta</Text></Box>
      <Box><Text inverse>inverse row</Text></Box>
      <Box><Text bold>bold row</Text></Box>
    </Box>,
    { stdout: out as unknown as NodeJS.WriteStream, debug: true,
      exitOnCtrlC: false, patchConsole: false });
  await flush();
  console.log("RAW:" + JSON.stringify(out.grab()).replace(/\\u001b/g, "\\e"));
  inst.unmount(); inst.cleanup();
});
