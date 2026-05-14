export type NodeStatus = "normal" | "elevated" | "alerted" | "high" | "flagged";

export interface MeshNode {
  id: number;
  suspicion: number;
  status: NodeStatus;
  flagged: boolean;
  neighbours: number[];
  detectors: number;
  detectorHits: number;
  sensitized: boolean;
  underAttack: boolean;
}

export interface MeshLink {
  source: number;
  target: number;
}

export interface MeshEvent {
  id: string;
  type: string;
  tick: number;
  message: string;
  node: number | null;
  target: number | null;
  source: number | null;
  nodes: number[];
  severity: "info" | "success" | "warning" | "critical" | "error";
}

export interface GossipCount {
  edge: string;
  count: number;
}

export interface MeshState {
  type: "state";
  tick: number;
  demoMode: boolean;
  nodes: MeshNode[];
  links: MeshLink[];
  events: MeshEvent[];
  eventLog: MeshEvent[];
  gossip: GossipCount[];
  memoryCells: number;
}

export interface PositionedNode extends MeshNode {
  x: number;
  y: number;
}
