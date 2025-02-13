import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

# Allow CORS for your frontend domains
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://your-frontend-domain.netlify.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GameState:
    def __init__(self):
        self.rooms = {}         # Holds room data
        self.player_rooms = {}  # Maps player IDs to room IDs

    def create_room(self, room_id, max_players=4):
        if room_id not in self.rooms:
            self.rooms[room_id] = {
                "players": {},
                "deck": self.initialize_deck(),
                "table_cards": [],  # Played cards
                "current_turn": None,
                "max_players": max_players,
                "game_started": False
            }
        return self.rooms[room_id]

    def initialize_deck(self):
        # Use lowercase suit names to match image file names
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))  # 1 (Ace) to 13 (King)
        deck = [{"suit": suit, "value": value} for suit in suits for value in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_id, player_id):
        room = self.rooms.get(room_id)
        if room and len(room["players"]) < room["max_players"]:
            room["players"][player_id] = {"hand": [], "score": 0}
            self.player_rooms[player_id] = room_id
            # Deal 5 cards to the player
            for _ in range(5):
                if room["deck"]:
                    room["players"][player_id]["hand"].append(room["deck"].pop())
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

    def reshuffle_deck(self, room):
        # When deck is empty, reshuffle all played cards except the top one
        if not room["deck"] and len(room["table_cards"]) > 1:
            top_card = room["table_cards"][-1]
            cards_to_reshuffle = room["table_cards"][:-1]
            random.shuffle(cards_to_reshuffle)
            room["deck"] = cards_to_reshuffle
            room["table_cards"] = [top_card]

game_state = GameState()

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
    room = game_state.create_room(room_id)
    if game_state.add_player(room_id, player_id):
        sio.enter_room(sid, room_id)
        await sio.emit("game_state", {
            "hand": room["players"][player_id]["hand"],
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "players": list(room["players"].keys())
        }, room=sid)
        await sio.emit("player_joined", {
            "player_id": player_id,
            "players": list(room["players"].keys())
        }, room=room_id, skip_sid=sid)

@sio.event
async def play_card(sid, data):
    """
    Expects: { player_id: <id>, card_indices: [list of indices] }
    If table_cards is not empty, each selected card must match the top card's value.
    """
    player_id = data.get("player_id")
    card_indices = data.get("card_indices")
    room_id = game_state.player_rooms.get(player_id)
    if room_id and room_id in game_state.rooms and isinstance(card_indices, list):
        room = game_state.rooms[room_id]
        if room["current_turn"] != player_id:
            await sio.emit("error", {"message": "Not your turn"}, room=sid)
            return
        player_hand = room["players"][player_id]["hand"]
        # Validate indices
        if any(i < 0 or i >= len(player_hand) for i in card_indices):
            await sio.emit("error", {"message": "Invalid card index"}, room=sid)
            return
        # If table is not empty, enforce that all played cards match the top card's value
        if room["table_cards"]:
            top_value = room["table_cards"][-1]["value"]
            for i in card_indices:
                if player_hand[i]["value"] !== top_value:
                    await sio.emit("error", {"message": "All played cards must match the top card"}, room=sid)
                    return
        # Remove selected cards; sort indices descending to avoid shifting
        card_indices = sorted(card_indices, reverse=True)
        played_cards = []
        for i in card_indices:
            played_cards.append(player_hand.pop(i))
        room["table_cards"].extend(played_cards)
        # Rotate turn (simple round-robin)
        players_list = list(room["players"].keys())
        current_index = players_list.index(player_id)
        next_index = (current_index + 1) % len(players_list)
        room["current_turn"] = players_list[next_index]
        await sio.emit("card_played", {
            "player_id": player_id,
            "played_cards": played_cards,
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"]
        }, room=room_id)
        await sio.emit("hand_updated", {"hand": player_hand}, room=sid)

@sio.event
async def draw_card(sid, data):
    player_id = data.get("player_id")
    room_id = game_state.player_rooms.get(player_id)
    if room_id and room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        if room["current_turn"] != player_id:
            await sio.emit("error", {"message": "Not your turn"}, room=sid)
            return
        if not room["deck"]:
            game_state.reshuffle_deck(room)
        if room["deck"]:
            card = room["deck"].pop()
            room["players"][player_id]["hand"].append(card)
            await sio.emit("card_drawn", {
                "hand": room["players"][player_id]["hand"],
                "deck_count": len(room["deck"])
            }, room=sid)
            await sio.emit("deck_updated", {"deck_count": len(room["deck"])}, room=room_id)
        else:
            await sio.emit("error", {"message": "No cards left in deck"}, room=sid)

@sio.event
async def call(sid, data):
    player_id = data.get("player_id")
    room_id = game_state.player_rooms.get(player_id)
    if room_id and room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        # Calculate hand totals for each player
        player_sums = {pid: sum(card["value"] for card in info["hand"]) for pid, info in room["players"].items()}
        caller_sum = player_sums.get(player_id, 0)
        lowest_sum = min(player_sums.values()) if player_sums else 0
        winners = [pid for pid, total in player_sums.items() if total == lowest_sum]
        if caller_sum == lowest_sum and len(winners) == 1:  # use proper equality in Python: ==
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
