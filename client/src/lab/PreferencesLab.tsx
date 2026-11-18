import { useState } from "preact/hooks";
import "../preferences/preferences.css";
import "./lab.css";
import { MORE_TOPICS, KINDS, REGIONS, SOURCES, STARTER_TOPICS, type MockSource } from "./mock-catalogue";
import { iconFor, PreferenceControl, type PreferenceState } from "../preferences/PreferenceControl";

// #332: hidden prototype of the preferences screen on mock data. State lives in memory only.

type Variant = "a" | "b" | "c";
type Prefs = Record<string, PreferenceState>;
type Tab = "sources" | "regions" | "topics";

const NOTE =
  "Hide works on every tab. Fewer and More change the order in Feed and Longer; News stays newest first.";

const EXPLAIN: Record<PreferenceState, string> = {
  hide: "Hide: never shown on any tab.",
  fewer: "Fewer: pushed down the order in Feed and Longer.",
  show: "Show: the normal order.",
  more: "More: pulled up the order in Feed and Longer.",
};

const sKey = (id: string) => `source:${id}`;
const rKey = (r: string) => `region:${r}`;
const tKey = (t: string) => `topic:${t}`;

function initialVariant(search: string): Variant {
  const v = new URLSearchParams(search).get("v");
  return v === "b" || v === "c" ? v : "a";
}

/** "2 hidden, 1 more, 1 fewer", or "No changes" when everything is on Show. */
export function summarise(prefs: Prefs): string {
  const n = (s: PreferenceState) => Object.values(prefs).filter((v) => v === s).length;
  const parts = ([["hide", "hidden"], ["more", "more"], ["fewer", "fewer"]] as const)
    .filter(([s]) => n(s) > 0)
    .map(([s, w]) => `${n(s)} ${w}`);
  return parts.length ? parts.join(", ") : "No changes";
}

interface Ctx {
  get: (k: string) => PreferenceState;
  set: (k: string, v: PreferenceState) => void;
  open: Set<string>;
  toggle: (id: string) => void;
}

function Row({ ctx, k, name }: { ctx: Ctx; k: string; name: string }) {
  return (
    <li class="lab-row">
      <span class="lab-name">{name}</span>
      <PreferenceControl label={name} value={ctx.get(k)} onChange={(v) => ctx.set(k, v)} />
    </li>
  );
}

function MoreButton({ ctx, id, count }: { ctx: Ctx; id: string; count: number }) {
  const expanded = ctx.open.has(id);
  return (
    <button type="button" class="lab-more" aria-expanded={expanded} onClick={() => ctx.toggle(id)}>
      {expanded ? "Show fewer" : `Show ${count} more`}
    </button>
  );
}

/** Sources not in the starter set, grouped by kind. */
function groupedByKind(rest: MockSource[], render: (s: MockSource) => preact.ComponentChild) {
  return KINDS.map((kind) => {
    const inKind = rest.filter((s) => s.kind === kind);
    if (inKind.length === 0) return null;
    return (
      <li key={kind} class="lab-kind">
        <h4>{kind}</h4>
        <ul class="lab-list">{inKind.map(render)}</ul>
      </li>
    );
  });
}

const starters = SOURCES.filter((s) => s.starter);
const others = SOURCES.filter((s) => !s.starter);

function VariantA({ ctx }: { ctx: Ctx }) {
  const row = (s: MockSource) => <Row key={s.id} ctx={ctx} k={sKey(s.id)} name={s.name} />;
  return (
    <>
      <section>
        <h2>Sources</h2>
        <ul class="lab-list">{starters.map(row)}</ul>
        {ctx.open.has("sources") ? <ul class="lab-list">{groupedByKind(others, row)}</ul> : null}
        <MoreButton ctx={ctx} id="sources" count={others.length} />
      </section>
      <section>
        <h2>Regions</h2>
        <ul class="lab-list">
          {REGIONS.map((r) => (
            <Row key={r} ctx={ctx} k={rKey(r)} name={r} />
          ))}
        </ul>
      </section>
      <section>
        <h2>Topics</h2>
        <TopicList ctx={ctx} />
      </section>
    </>
  );
}

function TopicList({ ctx }: { ctx: Ctx }) {
  const shown = ctx.open.has("topics") ? [...STARTER_TOPICS, ...MORE_TOPICS] : STARTER_TOPICS;
  return (
    <>
      <ul class="lab-list">
        {shown.map((t) => (
          <Row key={t} ctx={ctx} k={tKey(t)} name={t} />
        ))}
      </ul>
      <MoreButton ctx={ctx} id="topics" count={MORE_TOPICS.length} />
    </>
  );
}

function VariantB({ ctx }: { ctx: Ctx }) {
  const [tab, setTab] = useState<Tab>("sources");
  const [sheet, setSheet] = useState<{ k: string; name: string } | null>(null);
  const row = (k: string, name: string) => (
    <li key={k}>
      <button type="button" class="lab-tap" onClick={() => setSheet({ k, name })}>
        <span class="lab-name">{name}</span>
        <span class={`lab-state pref-${ctx.get(k)}`} aria-label={`currently ${ctx.get(k)}`}>
          {iconFor(ctx.get(k))}
        </span>
      </button>
    </li>
  );
  const topics = ctx.open.has("topics") ? [...STARTER_TOPICS, ...MORE_TOPICS] : STARTER_TOPICS;
  return (
    <>
      <div class="lab-tabs" role="tablist">
        {(["sources", "regions", "topics"] as const).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
          >
            {t[0]?.toUpperCase()}
            {t.slice(1)}
          </button>
        ))}
      </div>
      {tab === "sources" ? (
        <>
          <ul class="lab-list">{starters.map((s) => row(sKey(s.id), s.name))}</ul>
          {ctx.open.has("sources") ? (
            <ul class="lab-list">{groupedByKind(others, (s) => row(sKey(s.id), s.name))}</ul>
          ) : null}
          <MoreButton ctx={ctx} id="sources" count={others.length} />
        </>
      ) : null}
      {tab === "regions" ? <ul class="lab-list">{REGIONS.map((r) => row(rKey(r), r))}</ul> : null}
      {tab === "topics" ? (
        <>
          <ul class="lab-list">{topics.map((t) => row(tKey(t), t))}</ul>
          <MoreButton ctx={ctx} id="topics" count={MORE_TOPICS.length} />
        </>
      ) : null}
      {sheet ? (
        <div class="lab-backdrop" onClick={() => setSheet(null)}>
          <div
            class="lab-sheet"
            role="dialog"
            aria-modal="true"
            aria-label={sheet.name}
            onClick={(e) => e.stopPropagation()}
          >
            <h3>{sheet.name}</h3>
            <PreferenceControl
              label={sheet.name}
              value={ctx.get(sheet.k)}
              onChange={(v) => {
                ctx.set(sheet.k, v);
                setSheet(null);
              }}
            />
            <p class="lab-explain">{EXPLAIN[ctx.get(sheet.k)]}</p>
            <button type="button" class="lab-link" onClick={() => setSheet(null)}>
              Close
            </button>
          </div>
        </div>
      ) : null}
    </>
  );
}

function RegionGroup({ ctx, name, members }: { ctx: Ctx; name: string; members: MockSource[] }) {
  const id = `group:${name}`;
  const first = members.filter((s) => s.starter);
  const rest = members.filter((s) => !s.starter);
  const shown = ctx.open.has(id) ? members : first;
  return (
    <section class="lab-group">
      {name === "Everywhere" ? (
        <h2>Everywhere</h2>
      ) : (
        <div class="lab-group-head">
          <h2>{name}</h2>
          <PreferenceControl label={name} value={ctx.get(rKey(name))} onChange={(v) => ctx.set(rKey(name), v)} />
        </div>
      )}
      <ul class="lab-list lab-indent">
        {shown.map((s) => (
          <Row key={s.id} ctx={ctx} k={sKey(s.id)} name={s.name} />
        ))}
      </ul>
      {rest.length > 0 ? <MoreButton ctx={ctx} id={id} count={rest.length} /> : null}
    </section>
  );
}

function VariantC({ ctx }: { ctx: Ctx }) {
  return (
    <>
      <section>
        <h2>Topics</h2>
        <TopicList ctx={ctx} />
      </section>
      {REGIONS.map((r) => (
        <RegionGroup key={r} ctx={ctx} name={r} members={SOURCES.filter((s) => s.region === r)} />
      ))}
      <RegionGroup ctx={ctx} name="Everywhere" members={SOURCES.filter((s) => !s.region)} />
    </>
  );
}

const VARIANTS: { id: Variant; title: string }[] = [
  { id: "a", title: "One page" },
  { id: "b", title: "Tabs and sheet" },
  { id: "c", title: "Regions hold sources" },
];

/** The prototype screen at /lab/preferences. `?v=a|b|c` picks the layout, `?setup=1` the first-run flow. */
export function PreferencesLab() {
  const params = new URLSearchParams(location.search);
  const setup = params.get("setup") === "1";
  const [variant, setVariant] = useState<Variant>(() => initialVariant(location.search));
  const [prefs, setPrefs] = useState<Prefs>({});
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [started, setStarted] = useState(false);

  const ctx: Ctx = {
    get: (k) => prefs[k] ?? "show",
    set: (k, v) => setPrefs((p) => ({ ...p, [k]: v })),
    open,
    toggle: (id) =>
      setOpen((o) => {
        const n = new Set(o);
        if (!n.delete(id)) n.add(id);
        return n;
      }),
  };

  const choose = (v: Variant) => {
    setVariant(v);
    try {
      const p = new URLSearchParams(location.search);
      p.set("v", v);
      history.replaceState(null, "", `${location.pathname}?${p.toString()}`);
    } catch {
      // The address bar is a convenience; the variant still switches.
    }
  };

  return (
    <main class="lab">
      <nav class="lab-switch" aria-label="Layout variant">
        {VARIANTS.map((v) => (
          <button
            key={v.id}
            type="button"
            aria-pressed={variant === v.id}
            aria-label={`${v.id.toUpperCase()} ${v.title}`}
            onClick={() => choose(v.id)}
          >
            {v.id.toUpperCase()}
            <small>{` ${v.title}`}</small>
          </button>
        ))}
      </nav>
      <p class="lab-banner">Prototype on made-up data. Nothing is saved.</p>
      {setup ? (
        <header class="lab-welcome">
          <h1>Welcome</h1>
          <p>Choose what you see. You can change this any time in Settings.</p>
        </header>
      ) : (
        <h1>Preferences</h1>
      )}
      <p class="lab-note">{NOTE}</p>
      {variant === "a" ? <VariantA ctx={ctx} /> : null}
      {variant === "b" ? <VariantB ctx={ctx} /> : null}
      {variant === "c" ? <VariantC ctx={ctx} /> : null}
      <footer class="lab-foot">
        <p class="lab-summary">{summarise(prefs)}</p>
        <button
          type="button"
          class="lab-link"
          onClick={() => {
            setPrefs({});
            setOpen(new Set());
            setStarted(false);
          }}
        >
          Reset
        </button>
        {setup ? (
          started ? (
            <p role="status" class="lab-done">
              Setup finished (prototype: the real app would now open your feed).
            </p>
          ) : (
            <button type="button" class="lab-start" onClick={() => setStarted(true)}>
              Start reading
            </button>
          )
        ) : null}
      </footer>
    </main>
  );
}
