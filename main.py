import os
import random
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Update allowed origins with your deployed frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",                   # Local development
        "https://fadu-frontend.onrender.com"         # Deployed frontend URL on Render
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------
# Connection Manager for WebSocket
# -------------------------------
class ConnectionManager:
    def __init__(self):
        self.active_connections = {}  # Maps room_id to list of connections

    async def connect(self, room_id: str, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: str, websocket: WebSocket):
        if room_id in self.active_connections:
            self.active_connections[room_id].remove(websocket)
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_json(message)

manager = ConnectionManager()

# -------------------------------
# Global Game State
# -------------------------------
# Each room holds its own game state: deck, players, table card, current turn.
games = {}

def initialize_deck():
    suits = ["Hearts", "Diamonds", "Clubs", "Spades"]
    values = list(range(1, 14))
    deck = [{"suit": suit, "value": value} for suit in suits for value in values]
    random.shuffle(deck)
    return deck

@app.get("/")
async def read_root():
    return {"message": "Hello from Fadu backend!"}

@app.websocket("/ws/{room_id}/{user_id}")
async def websocket_endpoint(websocket, room_id: str, user_id: str):
    await manager.connect(room_id, websocket)
    try:
        # Create the game room if it doesn't exist.
        if room_id not in games:
            games[room_id] = {
                "deck": initialize_deck(),
                "players": {},
                "tableCard": None,
                "current_turn": None
            }
        game = games[room_id]

        # Add the player if not already present.
        if user_id not in game["players"]:
            game["players"][user_id] = {"hand": [], "score": 0}
            # Deal 5 cards to the new player.
            for _ in range(5):
                if game["deck"]:
                    game["players"][user_id]["hand"].append(game["deck"].pop())
            # Set the first player to join as the current turn.
            if game["current_turn"] is None:
                game["current_turn"] = user_id

        # Send a welcome message including the player's private hand.
        await manager.send_personal_message({
            "type": "welcome",
            "user": user_id,
            "message": f"Welcome to room {room_id}!",
            "hand": game["players"][user_id]["hand"],
            "current_turn": game["current_turn"]
        }, websocket)

        # Broadcast that a new player has joined (without revealing hands).
        await manager.broadcast(room_id, {
            "type": "player_joined",
            "user": user_id,
            "message": f"{user_id} has joined the game."
        })

        # Listen for incoming messages (actions) from the client.
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            action = message.get("action")

            if action == "draw_card":
                if game["deck"]:
                    card = game["deck"].pop()
                    game["players"][user_id]["hand"].append(card)
                    await manager.send_personal_message({
                        "type": "update_hand",
                        "hand": game["players"][user_id]["hand"],
                        "message": "Card drawn."
                    }, websocket)
                else:
                    await manager.send_personal_message({
                        "type": "error",
                        "message": "Deck is empty."
                    }, websocket)
            elif action == "play_card":
                card_index = message.get("cardIndex")
                if card_index is not None:
                    try:
                        card = game["players"][user_id]["hand"].pop(card_index)
                        game["tableCard"] = card
                        await manager.broadcast(room_id, {
                            "type": "table_update",
                            "tableCard": card,
                            "message": f"{user_id} played a card."
                        })
                        await manager.send_personal_message({
                            "type": "update_hand",
                            "hand": game["players"][user_id]["hand"],
                            "message": "Card played."
                        }, websocket)
                    except IndexError:
                        await manager.send_personal_message({
                            "type": "error",
                            "message": "Invalid card index."
                        }, websocket)
            elif action == "call":
                await manager.broadcast(room_id, {
                    "type": "call",
                    "message": f"{user_id} called!"
                })
            else:
                await manager.send_personal_message({
                    "type": "echo",
                    "message": f"Received unknown action: {action}"
                }, websocket)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        manager.disconnect(room_id, websocket)
        await manager.broadcast(room_id, {
            "type": "player_left",
            "user": user_id,
            "message": f"{user_id} has left the game."
        })

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
