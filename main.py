import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # or your Netlify domain if you want stricter CORS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GameState:
    def __init__(self):
        self.rooms = {}       # room_id -> room data
        self.player_rooms = {}  # player_id -> room_id

    def create_room(self, room_id, host_id, max_players=4):
        """Create a new room if it doesn't exist yet."""
        if room_id not in self.rooms:
            self.rooms[room_id] = {
                "players": {},         # { player_id: {...} }
                "deck": self.init_deck(),
                "table_cards": [],
                "current_turn": None,
                "max_players": max_players,
                "game_started": False,
                "host_id": host_id,
                "total_rounds": 1,
                "current_round": 1,
            }
        return self.rooms[room_id]

    def init_deck(self):
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))  # 1..13
        deck = [{"suit": s, "value": v} for s in suits for v in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_id, player_id, is_host=False):
        """Add a player to the room, up to max_players."""
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if len(room["players"]) < room["max_players"]:
                room["players"][player_id] = {
                    "hand": [],
                    "score": 0,
                    "is_host": is_host,
                    "has_drawn": False,
                }
                self.player_rooms[player_id] = room_id
                return True
        return False

    def remove_player(self, player_id):
        """Optional: handle disconnections, not shown here."""
        pass

    def deal_cards(self, room_id, player_id, count=5):
        """Deal `count` cards from the deck to the player's hand."""
        room = self.rooms[room_id]
        player = room["players"][player_id]
        for _ in range(count):
            if not room["deck"]:
                self.reshuffle_table(room_id)
            if room["deck"]:
                player["hand"].append(room["deck"].pop())

    def reshuffle_table(self, room_id):
        """If deck is empty, reshuffle table_cards except the top card."""
        room = self.rooms[room_id]
        if len(room["table_cards"]) <= 1:
            return
        top_card = room["table_cards"][-1]
        to_shuffle = room["table_cards"][:-1]
        random.shuffle(to_shuffle)
        room["deck"].extend(to_shuffle)
        room["table_cards"] = [top_card]

    def get_player_list(self, room_id):
        """Return a list of all player IDs in the room."""
        return list(self.rooms[room_id]["players"].keys())

    def broadcast_player_joined(self, room_id):
        """Emit 'player_joined' with the full updated list of players."""
        room = self.rooms[room_id]
        sio.start_background_task(sio.emit, 'player_joined', {
            "players": self.get_player_list(room_id),
            "host_id": room["host_id"],
        }, room=room_id)

    # ... Add your full game logic here (play_cards, call, next_round, etc.)
    # For brevity, let's focus on the join/start flow.

game_state = GameState()

@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

@sio.event
async def join_room(sid, data):
    """When the client calls socket.emit('join_room', {...})."""
    room_id = data.get('room_id')
    player_id = data.get('player_id')
    is_host = data.get('is_host', False)
    if not room_id or not player_id:
        await sio.emit('error', {"message": "Missing room_id or player_id"}, room=sid)
        return

    room = game_state.create_room(room_id, host_id=player_id if is_host else None)
    success = game_state.add_player(room_id, player_id, is_host=is_host)
    if not success:
        await sio.emit('error', {"message": "Room is full or invalid."}, room=sid)
        return

    sio.enter_room(sid, room_id)
    # Return the immediate game state to the new player
    current_room = game_state.rooms[room_id]
    await sio.emit('game_state', {
        "players": game_state.get_player_list(room_id),
        "table_cards": current_room["table_cards"],
        "current_turn": current_room["current_turn"],
        "deck_count": len(current_room["deck"]),
        "is_host": is_host,
        "game_status": "waiting",
        "current_round": current_room["current_round"],
    }, room=sid)

    # Broadcast updated player list to everyone
    game_state.broadcast_player_joined(room_id)

@sio.event
async def start_game(sid, data):
    room_id = data.get("room_id")
    total_rounds = data.get("total_rounds", 1)
    if room_id not in game_state.rooms:
        return
    room = game_state.rooms[room_id]

    # Only the host can start if not started yet and >=2 players
    if not room["game_started"] and len(room["players"]) >= 2:
        room["game_started"] = True
        room["total_rounds"] = total_rounds
        room["current_round"] = 1
        # Deal 5 cards to each player
        for pid in room["players"]:
            game_state.deal_cards(room_id, pid, count=5)
        # First player in the dict is current_turn
        room["current_turn"] = game_state.get_player_list(room_id)[0]
        print(f"Game started in room {room_id}, turn: {room['current_turn']}")

        # Emit 'game_started' with the new state
        await sio.emit('game_started', {
            "players": [
                {
                    "id": pid,
                    "hand": room["players"][pid]["hand"],
                    "score": room["players"][pid]["score"],
                }
                for pid in room["players"]
            ],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
            "current_round": room["current_round"],
        }, room=room_id)
    else:
        await sio.emit('error', {"message": "Game already started or not enough players."}, room=sid)

@app.get("/")
async def read_root():
    return {"message": "Fadu Card Game Backend (Unified)"}

# Mount Socket.IO
socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
