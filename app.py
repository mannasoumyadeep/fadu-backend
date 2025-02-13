import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

# Allow CORS for your frontend domain(s)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # local development
        "https://your-frontend-domain.netlify.app"  # deployed frontend URL
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Game state with improved logic
class GameState:
    def __init__(self):
        self.rooms = {}         # Holds room data
        self.player_rooms = {}  # Maps player IDs to room IDs

    def create_room(self, room_id, max_players=4):
        if room_id not in self.rooms:
            self.rooms[room_id] = {
                "players": {},
                "deck": self.initialize_deck(),
                "table_cards": [],  # List to hold played cards
                "current_turn": None,
                "max_players": max_players,
                "game_started": False
            }
        return self.rooms[room_id]

    def initialize_deck(self):
        # Use lowercase for suits to match image file names
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))  # 1 (Ace) to 13 (King)
        deck = [{"suit": suit, "value": value} for suit in suits for value in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_id, player_id):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if len(room["players"]) < room["max_players"]:
                room["players"][player_id] = {
                    "hand": [],
                    "score": 0
                }
                self.player_rooms[player_id] = room_id
                # Deal 5 cards to the player
                for _ in range(5):
                    if room["deck"]:
                        room["players"][player_id]["hand"].append(room["deck"].pop())
                # Set first player as current turn
                if room["current_turn"] is None:
                    room["current_turn"] = player_id
                return True
        return False

    def remove_player(self, player_id):
        if player_id in self.player_rooms:
            room_id = self.player_rooms[player_id]
            room = self.rooms[room_id]
            if player_id in room["players"]:
                del room["players"][player_id]
                del self.player_rooms[player_id]
                if room["current_turn"] == player_id:
                    players = list(room["players"].keys())
                    room["current_turn"] = players[0] if players else None
                if not room["players"]:
                    del self.rooms[room_id]
                return room_id
        return None

game_state = GameState()

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

@sio.event
async def join_room(sid, data):
    room_id = data.get('room_id')
    player_id = data.get('player_id')
    if not room_id or not player_id:
        await sio.emit('error', {"message": "Missing room_id or player_id"}, room=sid)
        return
    room = game_state.create_room(room_id)
    if game_state.add_player(room_id, player_id):
        sio.enter_room(sid, room_id)
        await sio.emit('game_state', {
            "hand": room["players"][player_id]["hand"],
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "players": list(room["players"].keys())
        }, room=sid)
        await sio.emit('player_joined', {
            "player_id": player_id,
            "players": list(room["players"].keys())
        }, room=room_id, skip_sid=sid)

@sio.event
async def play_card(sid, data):
    player_id = data.get('player_id')
    room_id = game_state.player_rooms.get(player_id)
    card_index = data.get('card_index')
    if room_id and room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        if room["current_turn"] == player_id and card_index is not None:
            try:
                player_hand = room["players"][player_id]["hand"]
                card = player_hand.pop(card_index)
                room["table_cards"].append(card)
                # Rotate turn to next player
                players = list(room["players"].keys())
                current_index = players.index(player_id)
                next_index = (current_index + 1) % len(players)
                room["current_turn"] = players[next_index]
                await sio.emit('card_played', {
                    "player_id": player_id,
                    "card": card,
                    "table_cards": room["table_cards"],
                    "current_turn": room["current_turn"]
                }, room=room_id)
                await sio.emit('hand_updated', {"hand": player_hand}, room=sid)
            except (IndexError, KeyError):
                await sio.emit('error', {"message": "Invalid card index"}, room=sid)

@sio.event
async def draw_card(sid, data):
    player_id = data.get('player_id')
    room_id = game_state.player_rooms.get(player_id)
    if room_id and room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        if room["current_turn"] == player_id and room["deck"]:
            card = room["deck"].pop()
            room["players"][player_id]["hand"].append(card)
            await sio.emit('card_drawn', {
                "hand": room["players"][player_id]["hand"],
                "deck_count": len(room["deck"])
            }, room=sid)
            await sio.emit('deck_updated', {"deck_count": len(room["deck"])}, room=room_id)

@sio.event
async def call(sid, data):
    player_id = data.get('player_id')
    room_id = game_state.player_rooms.get(player_id)
    if room_id and room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        # Calculate hand totals for each player
        player_sums = {}
        for pid, info in room["players"].items():
            player_sums[pid] = sum(card["value"] for card in info["hand"])
        caller_sum = player_sums.get(player_id, 0)
        lowest_sum = min(player_sums.values()) if player_sums else 0
        winners = [pid for pid, total in player_sums.items() if total == lowest_sum]
        if caller_sum == lowest_sum and len(winners) == 1:
            room["players"][player_id]["score"] += 2
            result = "win"
        else:
            room["players"][player_id]["score"] -= 1
            for pid in winners:
                room["players"][pid]["score"] += 1
            result = "loss"
        await sio.emit("call_result", {
            "result": result,
            "scores": {pid: info["score"] for pid, info in room["players"].items()},
            "player_sums": player_sums
        }, room=room_id)

@app.get("/")
async def read_root():
    return {"message": "Hello from Fadu backend!"}

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
