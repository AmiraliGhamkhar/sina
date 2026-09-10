import { useEffect, useState } from "react";

// Tailwind's `md` breakpoint — below this we treat the viewport as mobile.
export const MOBILE_BREAKPOINT = 768;

/** True while the viewport is narrower than `breakpoint` (live-updating). */
export function useIsMobile(breakpoint = MOBILE_BREAKPOINT): boolean {
  const [isMobile, setIsMobile] = useState(() =>
    typeof window !== "undefined" ? window.innerWidth < breakpoint : false
  );

  useEffect(() => {
    const mq = window.matchMedia(`(max-width: ${breakpoint - 1}px)`);
    const onChange = () => setIsMobile(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [breakpoint]);

  return isMobile;
}
