// 16px stroke icons used in the admin navigation (same shapes as the Polaris mockups)
const paths = {
  home: <path d="M3 9l7-6 7 6v8H3z" />,
  inbox: (
    <>
      <path d="M3 11l2-7h10l2 7v5H3z" />
      <path d="M3 11h4l1 2h4l1-2h4" />
    </>
  ),
  tag: (
    <>
      <path d="M3 3h7l7 7-7 7-7-7z" />
      <circle cx="7" cy="7" r="1.2" />
    </>
  ),
  truck: (
    <>
      <path d="M2 5h10v8H2zM12 8h3l3 3v2h-6z" />
      <circle cx="6" cy="15" r="1.5" />
      <circle cx="15" cy="15" r="1.5" />
    </>
  ),
  chart: <path d="M3 17V3M3 17h14M7 13V9M11 13V6M15 13v-3" />,
  settings: (
    <>
      <circle cx="10" cy="10" r="2.5" />
      <path d="M10 2v2.5M10 15.5V18M2 10h2.5M15.5 10H18M4.3 4.3l1.8 1.8M13.9 13.9l1.8 1.8M4.3 15.7l1.8-1.8M13.9 6.1l1.8-1.8" />
    </>
  ),
  search: (
    <>
      <circle cx="9" cy="9" r="5" />
      <path d="M13 13l4 4" />
    </>
  ),
};

export type IconName = keyof typeof paths;

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
