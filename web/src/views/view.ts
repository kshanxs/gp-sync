import type { Status } from "../lib/api";
import type { HowStep } from "../lib/page";

/** A page of the dashboard. main.ts draws its header the same way on every page (lib/page.ts). */
export interface View {
  /** One line: what the page is for. */
  subtitle?: string;
  /** "How it works", shown folded in the header. */
  how?: HowStep[];
  /** Build the page. `args` are the parts of the URL after the page name (#/settings/alerts). */
  mount(el: HTMLElement, args: string[]): void;
  /** Called with fresh status every 2 seconds while the page is open. */
  update?(s: Status): void;
  /** The URL changed but the page stayed (for example a settings section). */
  route?(args: string[]): void;
  unmount?(): void;
}
