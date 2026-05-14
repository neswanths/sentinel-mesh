import { useEffect, useRef, useState } from "react";
import { Terminal as TerminalIcon } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";

const WS_URL = "ws://127.0.0.1:8000/ws/attacker";
const PROMPT = "\x1b[38;2;168;218;220msentinel@mesh:~$\x1b[0m ";

type AckPayload = {
  type: "ack";
  level: "info" | "success" | "warning" | "error" | "muted";
  message: string;
};

type HelloPayload = {
  type: "hello";
  message: string;
};

type EventPayload = {
  type: "events";
  events: Array<{
    type: string;
    message: string;
    severity: string;
    tick: number;
  }>;
};

type SocketPayload = AckPayload | HelloPayload | EventPayload;

function colourFor(level: string) {
  if (level === "success") return "\x1b[38;2;84;242;139m";
  if (level === "warning") return "\x1b[38;2;233;196;106m";
  if (level === "error" || level === "critical") return "\x1b[38;2;230;57;70m";
  if (level === "muted") return "\x1b[38;2;136;142;160m";
  return "\x1b[38;2;80;190;245m";
}

export default function App() {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const bufferRef = useRef("");
  const cursorRef = useRef(0);
  const historyRef = useRef<string[]>([]);
  const historyIndexRef = useRef<number | null>(null);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    if (!hostRef.current || terminalRef.current) return;

    const terminal = new Terminal({
      cursorBlink: true,
      convertEol: true,
      fontFamily: '"JetBrains Mono", "Cascadia Code", monospace',
      fontSize: 15,
      lineHeight: 1.25,
      theme: {
        background: "#080713",
        foreground: "#F1FAEE",
        cursor: "#CFA6FF",
        selectionBackground: "#423268",
        black: "#080713",
        blue: "#50BEF5",
        cyan: "#A8DADC",
        green: "#54F28B",
        red: "#E63946",
        yellow: "#E9C46A",
        magenta: "#CFA6FF",
        white: "#F1FAEE",
      },
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(hostRef.current);
    fit.fit();
    terminalRef.current = terminal;
    fitRef.current = fit;

    writeLine("\x1b[38;2;80;190;245m[*]\x1b[0m Connecting to mesh network...");
    writePrompt();

    terminal.onData((data) => handleInput(data));
    const resize = () => fit.fit();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      terminal.dispose();
      terminalRef.current = null;
    };
  }, []);

  useEffect(() => {
    const socket = new WebSocket(WS_URL);
    socketRef.current = socket;
    socket.onopen = () => setConnected(true);
    socket.onclose = () => {
      setConnected(false);
      printSystem("warning", "Disconnected from mesh backend.");
    };
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data) as SocketPayload;
      if (payload.type === "hello") {
        printSystem("info", payload.message);
        printSystem("muted", "Type 'help' for available commands.");
      }
      if (payload.type === "ack" && payload.message) {
        printSystem(payload.level, payload.message);
      }
      if (payload.type === "events") {
        payload.events
          .filter((event) => event.type !== "GOSSIP")
          .forEach((event) => printSystem(event.severity, `[${event.type}] ${event.message}`));
      }
    };
    return () => socket.close();
  }, []);

  function term() {
    return terminalRef.current;
  }

  function writeLine(text: string) {
    term()?.writeln(text);
  }

  function writePrompt() {
    term()?.write(PROMPT);
    redrawInput();
  }

  function printSystem(level: string, message: string) {
    const terminal = term();
    if (!terminal) return;
    terminal.write("\r\n");
    terminal.writeln(`${colourFor(level)}[*]\x1b[0m ${message}`);
    terminal.write(PROMPT);
    redrawInput();
  }

  function redrawInput() {
    const terminal = term();
    if (!terminal) return;
    const buffer = bufferRef.current;
    const cursor = cursorRef.current;
    terminal.write("\x1b[2K\r");
    terminal.write(PROMPT + buffer);
    const stepsBack = buffer.length - cursor;
    if (stepsBack > 0) {
      terminal.write(`\x1b[${stepsBack}D`);
    }
  }

  function setBuffer(value: string, cursor = value.length) {
    bufferRef.current = value;
    cursorRef.current = Math.max(0, Math.min(cursor, value.length));
    redrawInput();
  }

  function submitCommand() {
    const command = bufferRef.current.trim();
    term()?.write("\r\n");
    if (command) {
      historyRef.current.push(command);
      historyIndexRef.current = null;
      if (socketRef.current?.readyState === WebSocket.OPEN) {
        socketRef.current.send(command);
      } else {
        writeLine(`${colourFor("error")}[*]\x1b[0m Backend is offline.`);
      }
    }
    bufferRef.current = "";
    cursorRef.current = 0;
    writePrompt();
  }

  function recallHistory(direction: "up" | "down") {
    const history = historyRef.current;
    if (!history.length) return;
    if (direction === "up") {
      historyIndexRef.current =
        historyIndexRef.current === null ? history.length - 1 : Math.max(0, historyIndexRef.current - 1);
    } else {
      if (historyIndexRef.current === null) return;
      historyIndexRef.current += 1;
      if (historyIndexRef.current >= history.length) {
        historyIndexRef.current = null;
        setBuffer("");
        return;
      }
    }
    setBuffer(history[historyIndexRef.current]);
  }

  function insertText(text: string) {
    const clean = text.replace(/\r?\n/g, "");
    const before = bufferRef.current.slice(0, cursorRef.current);
    const after = bufferRef.current.slice(cursorRef.current);
    setBuffer(before + clean + after, before.length + clean.length);
  }

  function handleInput(data: string) {
    if (data === "\r") {
      submitCommand();
      return;
    }
    if (data === "\u007f") {
      if (cursorRef.current === 0) return;
      const before = bufferRef.current.slice(0, cursorRef.current - 1);
      const after = bufferRef.current.slice(cursorRef.current);
      setBuffer(before + after, cursorRef.current - 1);
      return;
    }
    if (data === "\x1b[D") {
      cursorRef.current = Math.max(0, cursorRef.current - 1);
      redrawInput();
      return;
    }
    if (data === "\x1b[C") {
      cursorRef.current = Math.min(bufferRef.current.length, cursorRef.current + 1);
      redrawInput();
      return;
    }
    if (data === "\x1b[H" || data === "\x1b[1~" || data === "\x1bOH") {
      cursorRef.current = 0;
      redrawInput();
      return;
    }
    if (data === "\x1b[F" || data === "\x1b[4~" || data === "\x1bOF") {
      cursorRef.current = bufferRef.current.length;
      redrawInput();
      return;
    }
    if (data === "\x1b[A") {
      recallHistory("up");
      return;
    }
    if (data === "\x1b[B") {
      recallHistory("down");
      return;
    }
    if (data >= " " && data !== "\x7f") {
      insertText(data);
    }
  }

  return (
    <main className="attack-shell">
      <header className="attack-header">
        <div className="title-group">
          <TerminalIcon size={24} strokeWidth={1.8} />
          <h1>SENTINELMESH // ATTACK CONSOLE</h1>
        </div>
        <div className={connected ? "connection online" : "connection"}>
          <span />
          {connected ? "ONLINE" : "OFFLINE"}
        </div>
      </header>
      <section className="terminal-frame">
        <div ref={hostRef} className="terminal-host" />
      </section>
    </main>
  );
}
