"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, type IconName } from "./icons";

type NavItem = { href: string; label: string; icon: IconName };

const MAIN: NavItem[] = [
  { href: "/health", label: "Catalogue health", icon: "home" },
  { href: "/inbox", label: "Supplier inbox", icon: "inbox" },
  { href: "/products", label: "Products", icon: "tag" },
  { href: "/insights", label: "Insights", icon: "chart" },
];

const FOOTER: NavItem[] = [{ href: "/settings", label: "Settings", icon: "settings" }];

function isActive(pathname: string | null, href: string) {
  return pathname === href || (pathname?.startsWith(`${href}/`) ?? false);
}

/** Plain nav without an active item: the Suspense fallback while the pathname is unknown. */
export function NavList({ pathname }: { pathname: string | null }) {
  const link = (item: NavItem) => (
    <Link key={item.href} href={item.href} className={isActive(pathname, item.href) ? "on" : undefined}>
      <Icon name={item.icon} />
      <span>{item.label}</span>
    </Link>
  );
  return (
    <>
      {MAIN.map(link)}
      <div className="sep" />
      <div className="lbl">Sales channels</div>
      <Link href="/shop/SYN-00">
        <Icon name="tag" />
        <span>Online store</span>
      </Link>
      <div className="sep" />
      {FOOTER.map(link)}
    </>
  );
}

export function NavLinks() {
  return <NavList pathname={usePathname()} />;
}
