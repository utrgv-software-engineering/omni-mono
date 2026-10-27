import React from "react";
import { Text } from "ink";
import { render } from "ink-testing-library";
import { describe, expect, it } from "vitest";
import { useAppStore } from "../../store/appStore.js";
import { themedMemo } from "../themed-memo.js";

describe("themedMemo", () => {
  it("re-renders on a theme bump and skips unrelated parent renders", async () => {
    let renders = 0;
    const Leaf = themedMemo(({ label }: { label: string }) => {
      renders += 1;
      return <Text>{label}</Text>;
    });
    const Parent = ({ tick }: { tick: number }) => (
      <>
        <Text>{tick}</Text>
        <Leaf label="x" />
      </>
    );
    const app = render(<Parent tick={0} />);
    const settle = () => new Promise((r) => setTimeout(r, 20));
    await settle();
    expect(renders).toBe(1);
    app.rerender(<Parent tick={1} />);
    await settle();
    expect(renders).toBe(1);
    useAppStore.getState().actions.bumpThemeVersion();
    await settle();
    expect(renders).toBe(2);
    app.unmount();
  });
});
