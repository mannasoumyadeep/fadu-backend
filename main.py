import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI and Socket.IO app
app = FastAPI()
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=['*'])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict this to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------
# Game State and Logic
# --------------------------
class GameState:
    def __init__(self):
        self.rooms = {}
        self.player_rooms = {}

    def create_room(self, room_id):
        # Create a new room with default settings.
        self.rooms[room_id] = {
            "players": {},
            "deck": self.initialize_deck(),
            "table_cards": [],
            "current_turn": None,
            "max_players": 4,
            "game_started": False,
            "host_id": None,
            "total_rounds": 1,
            "current_round": 1,
        }
        return self.rooms[room_id]

    def initialize_deck(self):
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))  # Cards 1 to 13
        deck = [{"suit": s, "value": v} for s in suits for v in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_id, player_id):
        room = self.rooms.get(room_id)
        if not room:
            room = self.create_room(room_id)
        # If there are no players yet, set this player as host.
        is_host = (len(room["players"]) == 0)
        room["players"][player_id] = {
            "hand": [],
            "score": 0,
            "is_host": is_host,
            "has_drawn": False,
        }
        # Set host_id if first player.
        if is_host:
            room["host_id"] = player_id
        self.player_rooms[player_id] = room_id
        print(f"Player {player_id} joined room {room_id}. Host: {room['host_id']}")
        return room

    def deal_cards(self, room_id, player_id, count=5):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        for _ in range(count):
            if not room["deck"]:
                if not self.reshuffle_table_cards(room_id):
                    break
            if room["deck"]:
                player["hand"].append(room["deck"].pop())
        print(f"Dealt {count} card(s) to {player_id}. Hand size: {len(player['hand'])}")

    def reshuffle_table_cards(self, room_id):
        room = self.rooms[room_id]
        if len(room["table_cards"]) <= 1:
            return False
        top_card = room["table_cards"][-1]
        cards_to_shuffle = room["table_cards"][:-1]
        random.shuffle(cards_to_shuffle)
        room["deck"].extend(cards_to_shuffle)
        room["table_cards"] = [top_card]
        print(f"Reshuffled table into deck. New deck count: {len(room['deck'])}")
        return True

    def can_play(self, room_id, player_id, indices):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        hand = player["hand"]
        if not all(0 <= i < len(hand) for i in indices):
            return False
        selected = [hand[i] for i in indices]
        # First turn: table is empty → must draw first, then play exactly one card.
        if not room["table_cards"]:
            if not player["has_drawn"]:
                print("First turn: must draw a card before playing.")
                return False
            if len(selected) != 1:
                print("First turn: must play exactly one card.")
                return False
            return True
        # Otherwise, if table not empty:
        top_value = room["table_cards"][-1]["value"]
        has_match = any(card["value"] == top_value for card in hand)
        if has_match and not player["has_drawn"]:
            # Must play matching cards if available.
            if not all(card["value"] == top_value for card in selected):
                print("Must play matching card(s) if available.")
                return False
            return True
        else:
            # If no matching card OR player has drawn, then allow playing one card.
            if len(selected) != 1:
                print("After drawing, you must play exactly one card.")
                return False
            return True

    def play(self, player_id, indices):
        room_id = self.player_rooms[player_id]
        room = self.rooms[room_id]
        player = room["players"][player_id]
        if not self.can_play(room_id, player_id, indices):
            print(f"Invalid play by {player_id}.")
            return False
        for i in sorted(indices, reverse=True):
            card = player["hand"].pop(i)
            room["table_cards"].append(card)
        player["has_drawn"] = False
        # Advance turn
        players = list(room["players"].keys())
        idx = players.index(player_id)
        room["current_turn"] = players[(idx + 1) % len(players)]
        print(f"Player {player_id} played. Hand size now: {len(player['hand'])}")
        return True

    def calculate_call(self, caller_id):
        room_id = self.player_rooms.get(caller_id)
        if not room_id:
            return None
        room = self.rooms[room_id]
        sums = {pid: sum(card["value"] for card in p["hand"]) for pid, p in room["players"].items()}
        caller_sum = sums[caller_id]
        lowest = min(sums.values())
        winners = [pid for pid, s in sums.items() if s == lowest]
        if caller_sum == lowest and len(winners) == 1:
            room["players"][caller_id]["score"] += 3
            result = "win"
        else:
            room["players"][caller_id]["score"] -= 2
            for pid in winners:
                if pid != caller_id:
                    room["players"][pid]["score"] += 2
            result = "loss"
        print(f"Call by {caller_id}: {result}. Sums: {sums}")
        return {"result": result, "scores": {pid: p["score"] for pid, p in room["players"].items()}, "player_sums": sums}

    def next_round(self, room_id, round_winner=None):
        room = self.rooms[room_id]
        room["current_round"] += 1
        if room["current_round"] > room["total_rounds"]:
            return self.final_result(room_id)
        room["deck"] = self.initialize_deck()
        room["table_cards"] = []
        for pid in room["players"]:
            room["players"][pid]["hand"] = []
            room["players"][pid]["has_drawn"] = False
        for pid in room["players"]:
            self.deal_cards(room_id, pid, count=5)
        room["current_turn"] = round_winner if round_winner in room["players"] else list(room["players"].keys())[0]
        sio.start_background_task(self.emit_next_round, room_id)

    def emit_next_round(self, room_id):
        room = self.rooms[room_id]
        players = [{"id": pid, "hand": p["hand"], "score": p["score"]} for pid, p in room["players"].items()]
        sio.emit("next_round", {
            "players": players,
            "current_round": room["current_round"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
        }, room=room_id)

    def final_result(self, room_id):
        room = self.rooms[room_id]
        scores = {pid: p["score"] for pid, p in room["players"].items()}
        high = max(scores.values())
        winners = [pid for pid, s in scores.items() if s == high]
        data = {"scores": scores, "winners": winners}
        sio.start_background_task(sio.emit, "final_result", data, room=room_id)
        print(f"Final result for room {room_id}: {data}")
        return True

game_state = GameState()

# --------------------------
# Socket.IO Event Handlers
# --------------------------
@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    print(f"Client disconnected: {sid}")

@sio.event
async def join_room(sid, data):
    room_id = data.get("room_id")
    player_id = data.get("player_id")
    if not room_id or not player_id:
        await sio.emit("error", {"message": "Missing room_id or player_id"}, room=sid)
        return
    room = game_state.add_player(room_id, player_id)
    sio.enter_room(sid, room_id)
    # Emit updated game state to the joining client
    await sio.emit("game_state", {
        "hand": room["players"][player_id]["hand"],
        "table_cards": room["table_cards"],
        "current_turn": room["current_turn"],
        "players": list(room["players"].keys()),
        "is_host": room["host_id"] == player_id,
        "deck_count": len(room["deck"]),
        "game_status": "waiting",
        "current_round": room["current_round"],
    }, room=sid)
    # Notify everyone in the room
    await sio.emit("player_joined", {
        "players": list(room["players"].keys()),
        "host_id": room["host_id"],
    }, room=room_id)

@sio.event
async def start_game(sid, data):
    room_id = data.get("room_id")
    total_rounds = data.get("total_rounds", 1)
    if room_id not in game_state.rooms:
        return
    room = game_state.rooms[room_id]
    if room["game_started"]:
        await sio.emit("error", {"message": "Game already started."}, room=sid)
        return
    if len(room["players"]) < 2:
        await sio.emit("error", {"message": "Not enough players to start the game."}, room=sid)
        return
    room["game_started"] = True
    room["total_rounds"] = total_rounds
    room["current_round"] = 1
    for pid in room["players"]:
        game_state.deal_cards(room_id, pid, count=5)
    room["current_turn"] = list(room["players"].keys())[0]
    await sio.emit("game_started", {
        "players": [
            {"id": pid, "hand": room["players"][pid]["hand"], "score": room["players"][pid]["score"]}
            for pid in room["players"]
        ],
        "current_turn": room["current_turn"],
        "deck_count": len(room["deck"]),
        "current_round": room["current_round"],
    }, room=room_id)

@sio.event
async def draw_card(sid, data):
    player_id = data.get("player_id")
    if player_id not in game_state.player_rooms:
        await sio.emit("error", {"message": "Player not in any room."}, room=sid)
        return
    room_id = game_state.player_rooms[player_id]
    room = game_state.rooms[room_id]
    if room["current_turn"] != player_id:
        await sio.emit("error", {"message": "Not your turn to draw."}, room=sid)
        return
    if not room["deck"]:
        if not game_state.reshuffle_table_cards(room_id):
            await sio.emit("error", {"message": "Deck is empty and cannot be reshuffled."}, room=sid)
            return
    game_state.deal_cards(room_id, player_id, count=1)
    room["players"][player_id]["has_drawn"] = True
    await sio.emit("hand_updated", {
        "hand": room["players"][player_id]["hand"],
        "deck_count": len(room["deck"]),
    }, room=sid)
    await sio.emit("deck_updated", {"deck_count": len(room["deck"])}, room=room_id)

@sio.event
async def play_cards(sid, data):
    player_id = data.get("player_id")
    indices = data.get("card_indices", [])
    if not player_id or not indices:
        await sio.emit("error", {"message": "Missing player_id or card_indices"}, room=sid)
        return
    room_id = game_state.player_rooms.get(player_id)
    if room_id and game_state.play(player_id, indices):
        room = game_state.rooms[room_id]
        await sio.emit("cards_played", {
            "player_id": player_id,
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
        }, room=room_id)
        await sio.emit("hand_updated", {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"]),
        }, room=sid)
        if not room["players"][player_id]["hand"]:
            room["players"][player_id]["score"] += 4
            await sio.emit("round_won", {
                "player_id": player_id,
                "score": room["players"][player_id]["score"],
                "deck_count": len(room["deck"]),
            }, room=room_id)
            game_state.next_round(room_id, round_winner=player_id)
    else:
        await sio.emit("error", {"message": "Invalid play or move rejected."}, room=sid)

@sio.event
async def call(sid, data):
    player_id = data.get("player_id")
    if player_id in game_state.player_rooms:
        result = game_state.calculate_call(player_id)
        if result:
            room_id = game_state.player_rooms[player_id]
            await sio.emit("call_result", result, room=room_id)
            game_state.next_round(room_id, round_winner=None)
        else:
            await sio.emit("error", {"message": "Call could not be processed."}, room=sid)

@app.get("/")
async def read_root():
    return {"message": "Fadu Card Game Backend"}

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
