from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from simulation import simulation


@asynccontextmanager
async def lifespan(app: FastAPI):
    simulation.start()
    yield


app = FastAPI(title="SentinelMesh", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/observer")
async def observer_socket(websocket: WebSocket) -> None:
    await simulation.connect_observer(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await simulation.disconnect_observer(websocket)


@app.websocket("/ws/attacker")
async def attacker_socket(websocket: WebSocket) -> None:
    await simulation.connect_attacker(websocket)
    try:
        while True:
            command = await websocket.receive_text()
            response = await simulation.command(command)
            await websocket.send_json(response)
    except WebSocketDisconnect:
        await simulation.disconnect_attacker(websocket)
