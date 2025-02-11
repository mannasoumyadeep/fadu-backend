import os
import random
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://fadu-frontend.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections = {}
        self.game_states = {}  # Store game states separately

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
                # Clean up game state when room is empty
                if room_id in self.game_states:
                    del self.game_states[room_id]

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, room_id: str, message: dict):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_json(message)

    def get_player_count(self, room_id: str) -> int:
        return len(self.active_connections.get(room_id, []))

manager = ConnectionManager()

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
async def websocket_endpoint(websocket: WebSocket, room_id: str, user_id: str):
    await manager.connect(room_id, websocket)
    try:
        # Initialize game state for new room
        if room_id not in manager.game_states:
            manager.game_states[room_id] = {
                "deck": initialize_deck(),
                "players": {},
                "tableCard": None,
                "current_turn": None,
                "game_started": False,
                "max_players": 2  # Default max players
            }
        
        game = manager.game_states[room_id]

        # Add new player to the game
        if user_id not in game["players"]:
            # Check if room is full
            if len(game["players"]) >= game["max_players"]:
                await manager.send_personal_message({
                    "type": "error",
                    "message": "Room is full"
                }, websocket)
                await websocket.close()
                return

            game["players"][user_id] = {
                "hand": [],
                "score": 0,
                "ready": False
            }
            
            # Deal initial cards
            for _ in range(5):
                if game["deck"]:
                    game["players"][user_id]["hand"].append(game["deck"].pop())

            # First player becomes the current turn
            if game["current_turn"] is None:
                game["current_turn"] = user_id

        # Send initial game state to the new player
        await manager.send_personal_message({
            "type": "welcome",
            "user": user_id,
            "message": f"Welcome to room {room_id}!",
            "hand": game["players"][user_id]["hand"],
            "current_turn": game["current_turn"],
            "tableCard": game["tableCard"],
            "players_in_room": list(game["players"].keys())
        }, websocket)

        # Broadcast new player joined
        await manager.broadcast(room_id, {
            "type": "player_joined",
            "user": user_id,
            "message": f"{user_id} has joined the game",
            "players_in_room": list(game["players"].keys())
        })

        # Main game loop
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            action = message.get("action")

            if action == "draw_card" and game["current_turn"] == user_id:
                if game["deck"]:
                    card = game["deck"].pop()
                    game["players"][user_id]["hand"].append(card)
                    # Update player's hand
                    await manager.send_personal_message({
                        "type": "update_hand",
                        "hand": game["players"][user_id]["hand"],
                        "deck_count": len(game["deck"])
                    }, websocket)
                    # Broadcast deck count update
                    await manager.broadcast(room_id, {
                        "type": "deck_update",
                        "deck_count": len(game["deck"])
                    })
                else:
                    await manager.send_personal_message({
                        "type": "error",
                        "message": "Deck is empty"
                    }, websocket)

            elif action == "play_card" and game["current_turn"] == user_id:
                card_index = message.get("cardIndex")
                if card_index is not None:
                    try:
                        card = game["players"][user_id]["hand"].pop(card_index)
                        game["tableCard"] = card
                        
                        # Update the player's hand
                        await manager.send_personal_message({
                            "type": "update_hand",
                            "hand": game["players"][user_id]["hand"]
                        }, websocket)

                        # Broadcast the played card
                        await manager.broadcast(room_id, {
                            "type": "table_update",
                            "tableCard": card,
                            "played_by": user_id
                        })

                        # Move to next player
                        players = list(game["players"].keys())
                        current_index = players.index(user_id)
                        next_index = (current_index + 1) % len(players)
                        game["current_turn"] = players[next_index]

                        # Broadcast turn update
                        await manager.broadcast(room_id, {
                            "type": "turn_update",
                            "current_turn": game["current_turn"]
                        })
                    except IndexError:
                        await manager.send_personal_message({
                            "type": "error",
                            "message": "Invalid card index"
                        }, websocket)

            elif action == "call":
                await manager.broadcast(room_id, {
                    "type": "call",
                    "user": user_id,
                    "message": f"{user_id} called!"
                })

    except WebSocketDisconnect:
        manager.disconnect(room_id, websocket)
        if room_id in manager.game_states and user_id in manager.game_states[room_id]["players"]:
            del manager.game_states[room_id]["players"][user_id]
            # Reset turn if it was this player's turn
            if game["current_turn"] == user_id:
                players = list(game["players"].keys())
                if players:
                    game["current_turn"] = players[0]
            await manager.broadcast(room_id, {
                "type": "player_left",
                "user": user_id,
                "current_turn": game["current_turn"],
                "players_in_room": list(game["players"].keys())
            })

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)