import { useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";
import { forceCenter, forceLink, forceManyBody, forceSimulation } from "d3-force";
import type { MeshEvent, MeshState, PositionedNode } from "./types";

const WS_URL = "ws://127.0.0.1:8000/ws/observer";
const VIEWBOX_WIDTH = 1000;
const VIEWBOX_HEIGHT = 700;

const emptyState: MeshState = {
  type: "state",
  tick: 0,
  demoMode: true,
  nodes: [],
  links: [],
  events: [],
  eventLog: [],
  gossip: [],
  memoryCells: 0,
};

function statusColor(suspicion: number, flagged: boolean) {
  if (flagged || suspicion >= 1) return "#E63946";
  if (suspicion >= 0.7) return "#E76F51";
  if (suspicion >= 0.5) return "#F4A261";
  if (suspicion >= 0.3) return "#E9C46A";
  return "#457B9D";
}

function eventClass(type: string) {
  if (type === "APT_DETECTED" || type === "CONSENSUS_FLAG") return "critical";
  if (type === "DANGER_SIGNAL" || type === "ATTACK_STARTED") return "warning";
  if (type === "RESET" || type === "ATTACK_STOPPED") return "success";
  return "info";
}

function buildLayout(state: MeshState): PositionedNode[] {
  const nodes = state.nodes.map((node) => ({ ...node, x: 0, y: 0 }));
  const links = state.links.map((link) => ({ ...link }));
  const simulation = forceSimulation(nodes)
    .force("charge", forceManyBody().strength(-260))
    .force("center", forceCenter(VIEWBOX_WIDTH / 2, VIEWBOX_HEIGHT / 2))
    .force(
      "link",
      forceLink(links)
        .id((node) => (node as PositionedNode).id)
        .distance(118)
        .strength(0.74),
    )
    .stop();

  for (let i = 0; i < 320; i += 1) {
    simulation.tick();
  }

  return nodes.map((node) => ({
    ...node,
    x: Math.max(58, Math.min(VIEWBOX_WIDTH - 58, node.x ?? VIEWBOX_WIDTH / 2)),
    y: Math.max(58, Math.min(VIEWBOX_HEIGHT - 58, node.y ?? VIEWBOX_HEIGHT / 2)),
  }));
}

export default function App() {
  const [state, setState] = useState<MeshState>(emptyState);
  const [connected, setConnected] = useState(false);
  const [transientEvents, setTransientEvents] = useState<MeshEvent[]>([]);
  const layoutRef = useRef<PositionedNode[] | null>(null);

  useEffect(() => {
    const socket = new WebSocket(WS_URL);
    socket.onopen = () => setConnected(true);
    socket.onclose = () => setConnected(false);
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data) as MeshState;
      if (payload.type !== "state") return;
      setState(payload);
      if (payload.nodes.length && layoutRef.current === null) {
        layoutRef.current = buildLayout(payload);
      }
      if (payload.events.length) {
        setTransientEvents((events) => [...events, ...payload.events]);
      }
    };
    return () => socket.close();
  }, []);

  const nodes = useMemo(() => {
    const layout = layoutRef.current;
    if (!layout) return [];
    const byId = new Map(state.nodes.map((node) => [node.id, node]));
    return layout.map((positioned) => ({ ...positioned, ...byId.get(positioned.id) }));
  }, [state.nodes]);

  const nodeById = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const topSuspicion = [...state.nodes].sort((a, b) => b.suspicion - a.suspicion).slice(0, 5);
  const aptEvent = state.eventLog.find((event) => event.type === "APT_DETECTED" && state.tick - event.tick < 160);

  function removeTransient(id: string) {
    setTransientEvents((events) => events.filter((event) => event.id !== id));
  }

  return (
    <main className="observer-shell">
      {aptEvent ? <div className="apt-banner">APT_DETECTED · DISTRIBUTED PATTERN CONFIRMED</div> : null}
      <header className="instrument-header">
        <div className="header-copy">
          <h1>DISTRIBUTED THREAT MONITOR</h1>
          <p>NODE MESH · REAL-TIME ANALYSIS</p>
        </div>
        <div className="telemetry">
          <div>
            <span>TICK</span>
            <strong>{state.tick}</strong>
          </div>
          <span className={connected ? "status-light online" : "status-light"} />
          <button className="icon-button" aria-label="Close monitor">
            <X size={28} strokeWidth={1.8} />
          </button>
        </div>
      </header>

      <section className="observer-grid">
        <div className="mesh-stage">
          <svg viewBox={`0 0 ${VIEWBOX_WIDTH} ${VIEWBOX_HEIGHT}`} role="img" aria-label="SentinelMesh topology">
            <defs>
              <pattern id="dot-grid" width="26" height="26" patternUnits="userSpaceOnUse">
                <circle cx="2" cy="2" r="1.25" fill="#A8DADC" opacity="0.42" />
              </pattern>
              <filter id="node-glow" x="-50%" y="-50%" width="200%" height="200%">
                <feGaussianBlur stdDeviation="5" result="blur" />
                <feMerge>
                  <feMergeNode in="blur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            <rect width={VIEWBOX_WIDTH} height={VIEWBOX_HEIGHT} fill="url(#dot-grid)" />

            <g className="links">
              {state.links.map((link) => {
                const source = nodeById.get(link.source);
                const target = nodeById.get(link.target);
                if (!source || !target) return null;
                const flashing = transientEvents.some(
                  (event) =>
                    event.type === "GOSSIP" &&
                    ((event.source === link.source && event.target === link.target) ||
                      (event.source === link.target && event.target === link.source)),
                );
                return (
                  <line
                    key={`${link.source}-${link.target}`}
                    x1={source.x}
                    y1={source.y}
                    x2={target.x}
                    y2={target.y}
                    className={flashing ? "link gossip-flash" : "link"}
                  />
                );
              })}
            </g>

            <g className="transients">
              {transientEvents.map((event) => {
                if (event.type === "DANGER_SIGNAL" && event.node !== null) {
                  const node = nodeById.get(event.node);
                  if (!node) return null;
                  return (
                    <circle
                      key={event.id}
                      cx={node.x}
                      cy={node.y}
                      r="34"
                      className="danger-ring"
                      onAnimationEnd={() => removeTransient(event.id)}
                    />
                  );
                }
                if (event.type === "CONSENSUS_FLAG" && event.node !== null) {
                  const node = nodeById.get(event.node);
                  if (!node) return null;
                  return (
                    <circle
                      key={event.id}
                      cx={node.x}
                      cy={node.y}
                      r="42"
                      className="consensus-burst"
                      onAnimationEnd={() => removeTransient(event.id)}
                    />
                  );
                }
                return null;
              })}
            </g>

            <g className="nodes">
              {nodes.map((node) => (
                <g key={node.id} transform={`translate(${node.x} ${node.y})`}>
                  {node.suspicion >= 0.3 ? <circle r="30" className="node-pulse" /> : null}
                  <circle
                    r={node.neighbours.length >= 5 ? 23 : 20}
                    fill={statusColor(node.suspicion, node.flagged)}
                    className={node.sensitized ? "node sensitized" : "node"}
                    filter={node.suspicion >= 0.5 ? "url(#node-glow)" : undefined}
                  />
                  <text textAnchor="middle" dominantBaseline="central">{node.id}</text>
                </g>
              ))}
            </g>
          </svg>
        </div>

        <aside className="analysis-panel">
          <h2>Network Analysis</h2>
          <section>
            <h3>Top Suspicion Scores</h3>
            <div className="score-list">
              {topSuspicion.map((node) => (
                <div key={node.id} className={node.suspicion >= 0.5 ? "hot" : ""}>
                  <span>Node {node.id}</span>
                  <strong>{node.suspicion.toFixed(4)}</strong>
                </div>
              ))}
            </div>
          </section>

          <section>
            <h3>Recent Gossip Activity</h3>
            <div className="score-list mono">
              {state.gossip.length ? (
                state.gossip.slice(0, 5).map((item) => (
                  <div key={item.edge}>
                    <span>{item.edge.replace("->", "→")}</span>
                    <strong>{item.count} msgs</strong>
                  </div>
                ))
              ) : (
                <p className="muted">No gossip messages</p>
              )}
            </div>
          </section>

          <section>
            <h3>Event Log</h3>
            <div className="event-log">
              {state.eventLog.slice(0, 12).map((event) => (
                <article key={event.id} className={eventClass(event.type)}>
                  <span>{event.type}</span>
                  <p>{event.message}</p>
                </article>
              ))}
            </div>
          </section>
        </aside>
      </section>
    </main>
  );
}
