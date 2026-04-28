/** Linear, multi-page guided tour. ``navigateTo`` triggers a route change
 * before the step's anchor is highlighted. ``requiresAdmin`` steps are
 * filtered out for non-admin staff. */
export interface TourStep {
  /** CSS selector for the element to highlight. ``null`` = page-level step
   * (popover only, no anchor). */
  element: string | null;
  popover: {
    title: string;
    description: string;
    side?: "top" | "bottom" | "left" | "right";
    align?: "start" | "center" | "end";
  };
  navigateTo?: string;
  requiresAdmin?: boolean;
}

export const tourSteps: TourStep[] = [
  {
    element: null,
    popover: {
      title: "Welcome to Reserv",
      description:
        "Two-minute tour of the staff side of the queue management system. Skip with Esc anytime.",
    },
    navigateTo: "/",
  },
  {
    element: "header nav",
    popover: {
      title: "Top navigation",
      description:
        "Queues for live status, Analytics for trends, Admin for tooling. Each tab is its own page.",
      side: "bottom",
    },
  },
  {
    element: "main",
    popover: {
      title: "Live queues",
      description:
        "Each machine has a column. Cards show waiting users in order; staff actions (serve, bump, complete) act on the top card.",
      side: "right",
    },
  },
  {
    element: "header nav a[href='/admin/machines']",
    popover: {
      title: "Admin tools",
      description:
        "Manage machines, units, staff users, colleges, feedback, and settings — all gated by your role.",
      side: "bottom",
    },
    navigateTo: "/admin/machines",
  },
  {
    element: "main",
    popover: {
      title: "Machines",
      description:
        "Add or archive machines. Each has units (e.g. Main, Secondary) — the queue agent serves up to one job per active unit at a time.",
    },
  },
  {
    element: "header nav a[href='/admin/staff']",
    popover: {
      title: "Staff",
      description: "Invite teammates as staff or admin.",
      side: "bottom",
    },
    navigateTo: "/admin/staff",
    requiresAdmin: true,
  },
  {
    element: "header nav a[href='/admin/settings']",
    popover: {
      title: "Settings",
      description:
        "Tune reminder/grace timers, the queue reset hour, public mode, and feature flags like the data-analyst agent.",
      side: "bottom",
    },
    navigateTo: "/admin/settings",
    requiresAdmin: true,
  },
  {
    element: "header nav a[href='/analytics']",
    popover: {
      title: "Analytics",
      description:
        "Trend dashboard scoped to a period and optional college filter. CSV / PDF exports respect the active filters.",
      side: "bottom",
    },
    navigateTo: "/analytics",
  },
  {
    element: "main",
    popover: {
      title: "Analytics overview",
      description:
        "Summary cards, machine utilization, college breakdown, and a daily attendance line.",
    },
  },
  {
    element: "body",
    popover: {
      title: "Ask the data",
      description:
        "Bottom-right floating panel: a chat that grounds answers in the visible analytics blob — no hallucinations.",
      side: "left",
    },
  },
  {
    element: "body",
    popover: {
      title: "Build a chart",
      description:
        "Bottom-left floating panel (when enabled): a tool-calling agent that fetches real numbers and renders + pins custom charts.",
      side: "right",
    },
  },
];
