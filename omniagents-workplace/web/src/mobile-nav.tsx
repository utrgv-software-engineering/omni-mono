// Phones: each app's sidebar moves into its page-level drawer.
// Global app switching lives in WorkplaceNav. An app puts its navigation in <AppNav>, which is its sidebar from
// `md` up and, below, renders into the drawer (a portal into a node the
// drawer adopts while open, so the app's own state and handlers carry over).
import { createContext, useCallback, useContext } from "react";
import { createPortal } from "react-dom";

import { useIsMobile } from "@omniagents/ui/hooks/use-mobile";

/** Where an app's navigation goes on a phone: the drawer's body. */
export const NavSlot = createContext<HTMLElement | null>(null);

export function AppNav({
  className,
  drawerClassName = "flex flex-col gap-4",
  children,
}: {
  /** The sidebar, from `md` up. */
  className: string;
  /** The same navigation in the drawer. */
  drawerClassName?: string;
  children: React.ReactNode;
}) {
  const mobile = useIsMobile();
  const slot = useContext(NavSlot);
  if (!mobile) return <aside className={className}>{children}</aside>;
  return slot ? createPortal(<nav className={`text-sm ${drawerClassName}`}>{children}</nav>, slot) : null;
}

/** A ref for the drawer's body: it holds ``slot`` while it is open. */
export function useAdoptSlot(slot: HTMLElement) {
  return useCallback(
    (el: HTMLDivElement | null) => {
      if (el && slot.parentElement !== el) el.appendChild(slot);
    },
    [slot],
  );
}
