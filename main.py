import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI and Socket.IO app
app = FastAPI()
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # You can restrict this to your frontend domain if desired
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------
# Game State & Logic
# --------------------------
class GameState:
    def __init__(self):
        self.rooms = {}           # room_code -> room details
        self.player_rooms = {}    # player_id -> room_code

    def create_room(self, room_code):
        self.rooms[room_code] = {
            "players": {},         # player_id -> { hand, score, has_drawn }
            "deck": self.initialize_deck(),
            "table_cards": [],
            "current_turn": None,
            "host_id": None,
            "game_started": False,
            "total_rounds": 1,
            "current_round": 1,
        }
        return self.rooms[room_code]

    def initialize_deck(self):
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))
        deck = [{"suit": s, "value": v} for s in suits for v in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_code, player_id):
        # Create room if not exists.
        if room_code not in self.rooms:
            room = self.create_room(room_code)
        else:
            room = self.rooms[room_code]
        # If first player, assign as host.
        is_host = (len(room["players"]) == 0)
        room["players"][player_id] = {
            "hand": [],
            "score": 0,
            "has_drawn": False,
        }
        if is_host:
            room["host_id"] = player_id
        self.player_rooms[player_id] = room_code
        print(f"Player {player_id} joined room {room_code} (host: {room['host_id']})")
        return room, is_host

    def deal_cards(self, room_code, player_id, count=5):
        room = self.rooms[room_code]
        player = room["players"][player_id]
        for _ in range(count):
            if not room["deck"]:
                self.reshuffle(room_code)
                if not room["deck"]:
                    break
            player["hand"].append(room["deck"].pop())
        print(f"Dealt {count} card(s) to {player_id}. Hand size: {len(player['hand'])}")
        return player["hand"]

    def reshuffle(self, room_code):
        room = self.rooms[room_code]
        if len(room["table_cards"]) <= 1:
            return False
        top = room["table_cards"][-1]
        rest = room["table_cards"][:-1]
        random.shuffle(rest)
        room["deck"].extend(rest)
        room["table_cards"] = [top]
        print(f"Reshuffled table cards into deck; new deck count: {len(room['deck'])}")
        return True

    def can_play(self, room_code, player_id, indices):
        room = self.rooms[room_code]
        player = room["players"][player_id]
        hand = player["hand"]
        if not all(0 <= i < len(hand) for i in indices):
            return False
        selected = [hand[i] for i in indices]
        # If table is empty (first turn): must have drawn and play exactly one card.
        if not room["table_cards"]:
            if not player["has_drawn"]:
                print("First turn: player must draw a card before playing.")
                return False
            if len(selected) != 1:
                print("First turn: must play exactly one card.")
                return False
            return True
        # Otherwise, table not empty:
        top_val = room["table_cards"][-1]["value"]
        has_match = any(card["value"] == top_val for card in hand)
        if has_match and not player["has_drawn"]:
            # If matching card exists and player hasn't drawn, they must play matching card(s)
            if not all(card["value"] == top_val for card in selected):
                print("Player has matching card(s) but selected non-matching card(s).")
                return False
            return True
        else:
            # Either no matching card or player already drew → allow playing one card
            if len(selected) != 1:
                print("After drawing, must play exactly one card.")
                return False
            return True

    def play_cards(self, player_id, indices):
        room_code = self.player_rooms[player_id]
        room = self.rooms[room_code]
        player = room["players"][player_id]
        if not self.can_play(room_code, player_id, indices):
            print(f"Invalid play by {player_id}")
            return False
        for i in sorted(indices, reverse=True):
            card = player["hand"].pop(i)
            room["table_cards"].append(card)
        player["has_drawn"] = False
        # Advance turn
        players = list(room["players"].keys())
        idx = players.index(player_id)
        room["current_turn"] = players[(idx + 1) % len(players)]
        print(f"Player {player_id} played. New hand size: {len(player['hand'])}")
        return True

    def calculate_call(self, caller_id):
        room_code = self.player_rooms.get(caller_id)
        if not room_code:
            return None
        room = self.rooms[room_code]
        sums = {pid: sum(card["value"] for card in p["hand"]) for pid, p in room["players"].items()}
        caller_sum = sums[caller_id]
        low = min(sums.values())
        winners = [pid for pid, s in sums.items() if s == low]
        if caller_sum == low and len(winners) == 1:
            room["players"][caller_id]["score"] += 3
            result = "win"
        else:
            room["players"][caller_id]["score"] -= 2
            for pid in winners:
                if pid != caller_id:
                    room["players"][pid]["score"] += 2
            result = "loss"
        print(f"Call by {caller_id}: {result}. Sums: {sums}")
        return {"result": result, "scores": {pid: room["players"][pid]["score"] for pid in room["players"]}, "player_sums": sums}

    def next_round(self, room_code, round_winner=None):
        room = self.rooms[room_code]
        room["current_round"] += 1
        if room["current_round"] > room["total_rounds"]:
            return self.final_result(room_code)
        room["deck"] = self.initialize_deck()
        room["table_cards"] = []
        for pid in room["players"]:
            room["players"][pid]["hand"] = []
            room["players"][pid]["has_drawn"] = False
        for pid in room["players"]:
            self.deal_cards(room_code, pid, count=5)
        room["current_turn"] = round_winner if round_winner in room["players"] else list(room["players"].keys())[0]
        sio.emit("next_round", {
            "players": list(room["players"].keys()),
            "current_round": room["current_round"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"])
        }, room=room_code)

    def final_result(self, room_code):
        room = self.rooms[room_code]
        scores = {pid: p["score"] for pid, p in room["players"].items()}
        high = max(scores.values())
        winners = [pid for pid, s in scores.items() if s == high]
        data = {"scores": scores, "winners": winners}
        sio.emit("final_result", data, room=room_code)
        print(f"Final result for room {room_code}: {data}")
        return data

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
    room_code = data.get("room_id")
    player_id = data.get("player_id")
    if not room_code or not player_id:
        await sio.emit("error", {"message": "Missing room_id or player_id"}, room=sid)
        return
    room, _ = game_state.add_player(room_code, player_id)
    sio.enter_room(sid, room_code)
    # Send the current game state to the joining client.
    await sio.emit("game_state", {
        "hand": room["players"][player_id]["hand"],
        "table_cards": room["table_cards"],
        "current_turn": room["current_turn"],
        "players": list(room["players"].keys()),
        "host_id": room["host_id"],
        "deck_count": len(room["deck"]),
        "game_status": "waiting",
        "current_round": room["current_round"]
    }, room=sid)
    await sio.emit("player_joined", {
        "players": list(room["players"].keys()),
        "host_id": room["host_id"]
    }, room=room_code)

@sio.event
async def start_game(sid, data):
    room_code = data.get("room_id")
    total_rounds = data.get("total_rounds", 1)
    if room_code not in game_state.rooms:
        return
    room = game_state.rooms[room_code]
    if room["game_started"]:
        await sio.emit("error", {"message": "Game already started."}, room=sid)
        return
    if len(room["players"]) < 2:
        await sio.emit("error", {"message": "Not enough players."}, room=sid)
        return
    room["game_started"] = True
    room["total_rounds"] = total_rounds
    room["current_round"] = 1
    for pid in room["players"]:
        game_state.deal_cards(room_code, pid, count=5)
    room["current_turn"] = list(room["players"].keys())[0]
    await sio.emit("game_started", {
        "players": list(room["players"].keys()),
        "current_turn": room["current_turn"],
        "deck_count": len(room["deck"]),
        "current_round": room["current_round"]
    }, room=room_code)

@sio.event
async def draw_card(sid, data):
    player_id = data.get("player_id")
    if player_id not in game_state.player_rooms:
        await sio.emit("error", {"message": "Player not in room."}, room=sid)
        return
    room_code = game_state.player_rooms[player_id]
    room = game_state.rooms[room_code]
    if room["current_turn"] != player_id:
        await sio.emit("error", {"message": "Not your turn."}, room=sid)
        return
    if not room["deck"]:
        if not game_state.reshuffle(room_code):
            await sio.emit("error", {"message": "Deck empty and cannot reshuffle."}, room=sid)
            return
    game_state.deal_cards(room_code, player_id, count=1)
    room["players"][player_id]["has_drawn"] = True
    await sio.emit("hand_updated", {
        "hand": room["players"][player_id]["hand"],
        "deck_count": len(room["deck"])
    }, room=sid)
    await sio.emit("deck_updated", {"deck_count": len(room["deck"])}, room=room_code)

@sio.event
async def play_cards(sid, data):
    player_id = data.get("player_id")
    indices = data.get("card_indices", [])
    if not player_id or not indices:
        await sio.emit("error", {"message": "Missing parameters."}, room=sid)
        return
    if game_state.play_cards(player_id, indices):
        room_code = game_state.player_rooms[player_id]
        room = game_state.rooms[room_code]
        await sio.emit("cards_played", {
            "player_id": player_id,
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"])
        }, room=room_code)
        await sio.emit("hand_updated", {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        if not room["players"][player_id]["hand"]:
            room["players"][player_id]["score"] += 4
            await sio.emit("round_won", {
                "player_id": player_id,
                "score": room["players"][player_id]["score"],
                "deck_count": len(room["deck"])
            }, room=room_code)
            game_state.next_round(room_code, round_winner=player_id)
    else:
        await sio.emit("error", {"message": "Invalid play."}, room=sid)

@sio.event
async def call(sid, data):
    player_id = data.get("player_id")
    if player_id in game_state.player_rooms:
        result = game_state.calculate_call(player_id)
        if result:
            room_code = game_state.player_rooms[player_id]
            await sio.emit("call_result", result, room=room_code)
            game_state.next_round(room_code, round_winner=None)
        else:
            await sio.emit("error", {"message": "Call error."}, room=sid)

@app.get("/")
async def root():
    return {"message": "Fadu Card Game Backend"}

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
