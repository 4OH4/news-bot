import type { ComponentChildren } from "preact";

/** One detail in a details line (site, author, date, reading time, ...). */
export interface MetaItem {
  key: string;
  content: ComponentChildren;
  /** Let this item break between words like ordinary text (the author list). */
  wrap?: boolean;
}

/**
 * A details line rendered as separate items with dot separators, so the stylesheet can keep each
 * item whole on a line. The dot sits at the end of the preceding item, joined to it by a
 * non-breaking space, and an ordinary space after it is the only place the line may break, so no
 * line starts with a dot. Callers drop empty items.
 */
export function MetaLine({ className, items }: { className: string; items: MetaItem[] }) {
  return (
    <p class={className}>
      {items.map((item, i) => (
        <>
          {i > 0 ? " " : null}
          <span class={item.wrap ? "meta-item meta-wrap" : "meta-item"} key={item.key}>
            {item.content}
            {i < items.length - 1 ? " ·" : null}
          </span>
        </>
      ))}
    </p>
  );
}
