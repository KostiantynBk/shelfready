import Link from "next/link";
import { Suspense } from "react";
import { Icon } from "./icons";
import { NavLinks, NavList } from "./NavLinks";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <>
      <header className="top">
        <Link href="/health" className="brand">
          <span className="mark">S</span>ShelfReady
        </Link>
        <div className="search" role="search">
          <Icon name="search" />
          <span>Search products, suppliers, issues</span>
          <span className="kbd">Ctrl K</span>
        </div>
        <div className="store">
          Casa Nord<span className="av">CN</span>
        </div>
      </header>
      <div className="frame">
        <nav className="nav" aria-label="Main">
          {/* usePathname suspends on dynamic routes when cacheComponents is on */}
          <Suspense fallback={<NavList pathname={null} />}>
            <NavLinks />
          </Suspense>
        </nav>
        <main className="page">
          <div className="wrap">{children}</div>
        </main>
      </div>
    </>
  );
}
