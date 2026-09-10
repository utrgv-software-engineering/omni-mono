import React from "react";
import { useThemeVersion } from "../store/appStore.js";

/**
 * `React.memo` for components that paint with `Theme`.
 *
 * `Theme` is read at render time, so a theme switch only shows on
 * components that render again. Plain components do, because the root
 * re-renders on the theme version; memoized ones would not, since their
 * props did not change. This wrapper subscribes the memo boundary to the
 * theme version so it re-renders exactly when the palette changes and
 * keeps skipping every other update, which is what memo was for.
 */
export function themedMemo<P extends object>(
  Component: React.FunctionComponent<P>,
  propsAreEqual?: (prev: Readonly<P>, next: Readonly<P>) => boolean,
): React.NamedExoticComponent<P> {
  const Themed: React.FunctionComponent<P> = (props) => {
    useThemeVersion();
    return Component(props);
  };
  Themed.displayName = Component.displayName ?? Component.name;
  return React.memo(Themed, propsAreEqual);
}
