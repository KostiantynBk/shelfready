// Small Polaris-style building blocks; class names match design/mockups-polaris

export function PageHeader({
  title,
  meta,
  actions,
}: {
  title: string;
  meta?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <>
      <div className="ph">
        <h1>{title}</h1>
        {actions && <div className="acts">{actions}</div>}
      </div>
      {meta && <p className="meta">{meta}</p>}
    </>
  );
}

export function Card({
  title,
  aside,
  children,
}: {
  title?: React.ReactNode;
  aside?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="card">
      {title && (
        <div className="ch">
          {title}
          {aside && <span className="r">{aside}</span>}
        </div>
      )}
      <div className="cb">{children}</div>
    </section>
  );
}

export type Tone = "crit" | "succ" | "att" | "warn" | "info";

export function Badge({ tone, children }: { tone?: Tone; children: React.ReactNode }) {
  return <span className={tone ? `bd ${tone}` : "bd"}>{children}</span>;
}

export function EmptyState({ heading, children }: { heading: string; children?: React.ReactNode }) {
  return (
    <div className="empty">
      <h2>{heading}</h2>
      {children && <p className="muted">{children}</p>}
    </div>
  );
}
