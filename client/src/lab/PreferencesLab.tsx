import { useState } from "preact/hooks";
import "./lab.css";
import { MORE_TOPICS, KINDS, REGIONS, SOURCES, STARTER_TOPICS, type MockSource } from "./mock-catalogue";
import { PreferenceControl, type PreferenceState } from "./PreferenceControl";

// #332: hidden prototype of the preferences screen on mock data. State lives in memory only.

type Prefs = Record<string, PreferenceState>;

const NOTE =
  "Hide works on every tab. Fewer and More change the order in Feed and Longer; News stays newest first.";

const sKey = (id: string) => `source:${id}`;
const rKey = (r: string) => `region:${r}`;
const tKey = (t: string) => `topic:${t}`;

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

/** The prototype screen at /lab/preferences. `?setup=1` the first-run flow. */
export function PreferencesLab() {
  const params = new URLSearchParams(location.search);
  const setup = params.get("setup") === "1";
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

  return (
    <main class="lab">
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
      <VariantA ctx={ctx} />
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
