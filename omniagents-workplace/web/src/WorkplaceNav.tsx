import { OmniRailAvatar } from "@omniagents/chat-ui/components/OmniRailAvatar";
import { useEffect, useState, type ReactNode } from "react";
import { MoreHorizontal, type LucideIcon } from "lucide-react";
import { Button } from "@omniagents/ui/button";
import { cn } from "@omniagents/ui/cn";
import { useIsMobile } from "@omniagents/ui/hooks/use-mobile";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetTrigger,
} from "@omniagents/ui/sheet";

export interface WorkplaceSection {
  name: string;
  to: string;
  icon: LucideIcon;
  active: boolean;
}
// Hide phone navigation only when an editable field has a shrunken viewport.
// Focusing with a hardware keyboard must leave navigation available.
function useKeyboardOpen(mobile: boolean) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const viewport = window.visualViewport;
    let width = window.innerWidth;
    let baseline = window.innerHeight;
    const update = () => {
      if (width !== window.innerWidth) {
        width = window.innerWidth;
        baseline = window.innerHeight;
      }
      const field = document.activeElement;
      const editing =
        field instanceof HTMLElement &&
        (field.isContentEditable ||
          field.matches(
            "textarea:not([readonly]), input:not([readonly]):not([type=checkbox]):not([type=radio]):not([type=button]):not([type=submit])",
          ));
      if (!editing) baseline = Math.max(baseline, window.innerHeight);
      const height = viewport?.height ?? window.innerHeight;
      const keyboardOpen = mobile && !!editing && baseline - height > 150;
      setOpen(keyboardOpen);
      if (keyboardOpen) {
        document.documentElement.style.setProperty(
          "--workplace-viewport-height",
          `${height}px`,
        );
      } else {
        document.documentElement.style.removeProperty(
          "--workplace-viewport-height",
        );
      }
    };
    update();
    viewport?.addEventListener("resize", update);
    window.addEventListener("resize", update);
    document.addEventListener("focusin", update);
    document.addEventListener("focusout", update);
    return () => {
      viewport?.removeEventListener("resize", update);
      window.removeEventListener("resize", update);
      document.removeEventListener("focusin", update);
      document.removeEventListener("focusout", update);
      document.documentElement.style.removeProperty(
        "--workplace-viewport-height",
      );
    };
  }, [mobile]);
  return open;
}

export function WorkplaceNav({
  sections,
  brand,
  personalMode = false,
  navigate,
  account,
}: {
  sections: WorkplaceSection[];
  brand: string;
  personalMode?: boolean;
  navigate: (to: string) => void;
  account: (closeNavigation: () => void) => ReactNode;
}) {
  const mobile = useIsMobile();
  const keyboard = useKeyboardOpen(mobile);
  const [more, setMore] = useState(false);
  useEffect(() => {
    setMore(false);
  }, [mobile, sections.find((s) => s.active)?.to]);
  const personal = personalMode
    ? sections.find((section) => section.to === "/personal")
    : undefined;
  const primary = sections.slice(0, 3);
  const remaining = sections.slice(3);
  const link = (section: WorkplaceSection, compact: boolean) => (
    <Button
      key={section.to}
      asChild
      variant={section.active ? "secondary" : "ghost"}
      className={cn(
        "min-w-0",
        compact ? "h-14 w-full flex-col gap-1 px-1" : "h-12 justify-start",
      )}
    >
      <a
        href={section.to}
        aria-current={section.active ? "page" : undefined}
        onClick={(event) => {
          if (
            event.button ||
            event.metaKey ||
            event.ctrlKey ||
            event.shiftKey ||
            event.altKey
          )
            return;
          event.preventDefault();
          setMore(false);
          navigate(section.to);
        }}
      >
        <section.icon aria-hidden="true" />
        <span className={cn("truncate", compact && "text-[11px]")}>
          {section.name}
        </span>
      </a>
    </Button>
  );
  if (!mobile)
    return (
      <aside
        aria-label="Workplace navigation"
        className="flex w-[76px] shrink-0 flex-col border-r bg-background pb-[env(safe-area-inset-bottom)] pt-[env(safe-area-inset-top)]"
      >
        <div
          className="flex h-14 shrink-0 items-center justify-center"
          title={personal ? undefined : brand}
        >
          {personal ? (
            !personal.active && (
              <a
                href="/personal"
                aria-label="Open Omni"
                title="Open Omni"
                className="rounded-xl focus-visible:outline-2 focus-visible:outline-ring"
                onClick={(event) => {
                  if (
                    event.button ||
                    event.metaKey ||
                    event.ctrlKey ||
                    event.shiftKey ||
                    event.altKey
                  )
                    return;
                  event.preventDefault();
                  navigate("/personal");
                }}
              >
                <OmniRailAvatar />
              </a>
            )
          ) : (
            <span
              aria-label={brand}
              className="flex size-9 items-center justify-center rounded-xl bg-primary font-semibold text-primary-foreground"
            >
              {brand.slice(0, 1)}
            </span>
          )}
        </div>
        <nav
          aria-label="Apps"
          className="flex min-h-0 flex-1 flex-col overflow-y-auto px-1.5 py-2"
        >
          <div className="my-auto flex shrink-0 flex-col gap-1">
            {sections.map((s) => link(s, true))}
          </div>
        </nav>
        <div className="flex shrink-0 flex-col gap-1 border-t px-1.5 py-2">
          {account(() => {})}
        </div>
      </aside>
    );

  return (
    <Sheet open={more} onOpenChange={setMore}>
      <nav
        aria-label="Workplace navigation"
        className={cn(
          "order-last grid shrink-0 grid-cols-4 gap-1 border-t bg-background px-2 pt-1 pb-[max(0.25rem,env(safe-area-inset-bottom))]",
          keyboard && "hidden",
        )}
      >
        {primary.map((s) => link(s, true))}
        <SheetTrigger asChild>
          <Button
            variant={remaining.some((s) => s.active) ? "secondary" : "ghost"}
            className="h-14 flex-col gap-1 px-1"
            aria-label="More apps and settings"
          >
            <MoreHorizontal aria-hidden="true" />
            <span className="text-[11px]">More</span>
          </Button>
        </SheetTrigger>
      </nav>
      <SheetContent
        side="bottom"
        className="max-h-[85dvh] overflow-y-auto rounded-t-xl pb-[max(1rem,env(safe-area-inset-bottom))]"
      >
        <SheetHeader>
          <SheetTitle>More</SheetTitle>
          <SheetDescription>Apps and settings for {brand}</SheetDescription>
        </SheetHeader>
        <nav aria-label="More apps" className="grid grid-cols-2 gap-2 px-4">
          {remaining.map((s) => link(s, false))}
        </nav>
        <div className="mx-4 border-t pt-3">
          {account(() => setMore(false))}
        </div>
      </SheetContent>
    </Sheet>
  );
}
