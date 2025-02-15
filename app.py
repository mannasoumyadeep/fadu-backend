import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI and Socket.IO app
app = FastAPI()
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GameState:
    def __init__(self):
        self.rooms = {}
        self.player_rooms = {}

    def create_room(self, room_id, host_id, max_players=4):
        if room_id not in self.rooms:
            self.rooms[room_id] = {
                "players": {},
                "deck": self.initialize_deck(),
                "table_cards": [],
                "current_turn": None,
                "max_players": max_players,
                "game_started": False,
                "host_id": host_id,
                "total_rounds": 1,
                "current_round": 1,
            }
        return self.rooms[room_id]

    def initialize_deck(self):
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))  # Cards 1 (Ace) to 13 (King)
        deck = [{"suit": suit, "value": value} for suit in suits for value in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_id, player_id, is_host=False):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if len(room["players"]) < room["max_players"]:
                room["players"][player_id] = {
                    "hand": [],
                    "score": 0,
                    "is_host": is_host,
                    "has_drawn": False,  # Flag indicating whether the player has drawn a card this turn
                }
                self.player_rooms[player_id] = room_id
                # If game already started, deal 5 cards to new players
                if room["game_started"]:
                    self.deal_cards(room_id, player_id, count=5)
                if room["current_turn"] is None and room["game_started"]:
                    room["current_turn"] = player_id
                print(f"Player {player_id} added to room {room_id}.")
                return True
        return False

    def deal_cards(self, room_id, player_id, count=5):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        for _ in range(count):
            if not room["deck"]:
                if not self.reshuffle_table_cards(room_id):
                    break
            if room["deck"]:
                player["hand"].append(room["deck"].pop())
        print(f"Dealt {count} card(s) to {player_id}. Hand now: {len(player['hand'])}")

    def reshuffle_table_cards(self, room_id):
        room = self.rooms[room_id]
        if len(room["table_cards"]) <= 1:
            return False
        top_card = room["table_cards"][-1]
        cards_to_shuffle = room["table_cards"][:-1]
        random.shuffle(cards_to_shuffle)
        room["deck"].extend(cards_to_shuffle)
        room["table_cards"] = [top_card]
        print(f"Reshuffled table cards into deck; deck count: {len(room['deck'])}")
        return True

    def can_play_cards(self, room_id, player_id, card_indices):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        # Validate indices are within the hand
        if not all(0 <= idx < len(player["hand"]) for idx in card_indices):
            return False
        selected_cards = [player["hand"][idx] for idx in card_indices]
        # FIRST TURN (table is empty): the first player must have drawn before playing,
        # and may only play exactly one card.
        if not room["table_cards"]:
            if not player["has_drawn"]:
                print(f"Player {player_id} must draw a card first (table is empty).")
                return False
            if len(selected_cards) != 1:
                print(f"Player {player_id} must play exactly one card on the first turn.")
                return False
            return True
        else:
            top_value = room["table_cards"][-1]["value"]
            # Check if the player has any card matching the top card's value in their hand.
            has_match = any(card["value"] == top_value for card in player["hand"])
            if has_match:
                # If the player has a matching card and has not drawn, they must play only matching cards.
                if not player["has_drawn"]:
                    if not all(card["value"] == top_value for card in selected_cards):
                        print(f"Player {player_id} has matching card(s) but selected non-matching card(s).")
                        return False
                    # They may play one or more matching cards.
                    return True
                else:
                    # If they have drawn (choosing not to play a matching card), they are allowed to play any one card.
                    if len(selected_cards) != 1:
                        print(f"Player {player_id} after drawing must play exactly one card.")
                        return False
                    return True
            else:
                # If the player does not have any matching card, they must have drawn first.
                if not player["has_drawn"]:
                    print(f"Player {player_id} has no matching card and must draw first.")
                    return False
                if len(selected_cards) != 1:
                    print(f"Player {player_id} after drawing must play exactly one card.")
                    return False
                return True

    def play_cards(self, player_id, card_indices):
        if player_id not in self.player_rooms:
            return False
        room_id = self.player_rooms[player_id]
        room = self.rooms[room_id]
        player = room["players"][player_id]
        if not self.can_play_cards(room_id, player_id, card_indices):
            print(f"Play rejected for player {player_id} with card indices {card_indices}")
            return False
        # Remove selected card(s) from the hand (largest indices first) and add to table
        for idx in sorted(card_indices, reverse=True):
            card = player["hand"].pop(idx)
            room["table_cards"].append(card)
        # Reset the draw flag after playing
        player["has_drawn"] = False
        print(f"Player {player_id} played card(s). New hand count: {len(player['hand'])}")
        # Advance turn to next player
        players = list(room["players"].keys())
        current_index = players.index(player_id)
        next_index = (current_index + 1) % len(players)
        room["current_turn"] = players[next_index]
        return True

    def calculate_call_result(self, caller_id):
        if caller_id not in self.player_rooms:
            return None
        room_id = self.player_rooms[caller_id]
        room = self.rooms[room_id]
        player_sums = {pid: sum(card["value"] for card in player["hand"]) for pid, player in room["players"].items()}
        caller_sum = player_sums[caller_id]
        lowest_sum = min(player_sums.values())
        winners = [pid for pid, s in player_sums.items() if s == lowest_sum]
        if caller_sum == lowest_sum and len(winners) == 1:
            room["players"][caller_id]["score"] += 3
            result = "win"
        else:
            room["players"][caller_id]["score"] -= 2
            for pid in winners:
                if pid != caller_id:
                    room["players"][pid]["score"] += 2
            result = "loss"
        print(f"Call result for {caller_id}: {result}, sums: {player_sums}")
        return {
            "result": result,
            "scores": {pid: p["score"] for pid, p in room["players"].items()},
            "player_sums": player_sums
        }

    def next_round(self, room_id, round_winner=None):
        """Start next round if total_rounds not reached; otherwise, emit final result."""
        room = self.rooms[room_id]
        room["current_round"] += 1
        if room["current_round"] > room["total_rounds"]:
            return self.send_final_result(room_id)
        # Reset deck and table; clear each player's hand and draw 5 new cards.
        room["deck"] = self.initialize_deck()
        room["table_cards"] = []
        for pid in room["players"]:
            room["players"][pid]["hand"] = []
            room["players"][pid]["has_drawn"] = False
        for pid in room["players"]:
            self.deal_cards(room_id, pid, count=5)
        # Round winner (if any) starts next round; otherwise, first player.
        if round_winner and round_winner in room["players"]:
            room["current_turn"] = round_winner
        else:
            room["current_turn"] = list(room["players"].keys())[0]
        sio.start_background_task(self.emit_next_round, room_id)

    def send_final_result(self, room_id):
        room = self.rooms[room_id]
        scores = {pid: p["score"] for pid, p in room["players"].items()}
        high_score = max(scores.values())
        winners = [pid for pid, s in scores.items() if s == high_score]
        final_data = {"scores": scores, "winners": winners}
        sio.start_background_task(sio.emit, "final_result", final_data, room=room_id)
        print(f"Final result for room {room_id}: {final_data}")
        return True

    def emit_next_round(self, room_id):
        room = self.rooms[room_id]
        players_data = []
        for pid in room["players"]:
            players_data.append({
                "id": pid,
                "hand": room["players"][pid]["hand"],
                "score": room["players"][pid]["score"],
            })
        sio.emit("next_round", {
            "players": players_data,
            "current_round": room["current_round"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
        }, room=room_id)

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
    is_host = data.get("is_host", False)
    if not room_id or not player_id:
        await sio.emit("error", {"message": "Missing room_id or player_id"}, room=sid)
        return
    room = game_state.create_room(room_id, player_id if is_host else None)
    if game_state.add_player(room_id, player_id, is_host):
        sio.enter_room(sid, room_id)
        current_room = game_state.rooms[room_id]
        await sio.emit("game_state", {
            "hand": current_room["players"][player_id]["hand"],
            "table_cards": current_room["table_cards"],
            "current_turn": current_room["current_turn"],
            "players": list(current_room["players"].keys()),
            "is_host": is_host,
            "deck_count": len(current_room["deck"])
        }, room=sid)
        await sio.emit("player_joined", {
            "player_id": player_id,
            "players": list(current_room["players"].keys()),
            "host_id": current_room["host_id"]
        }, room=room_id)
    else:
        await sio.emit("error", {"message": "Unable to join room."}, room=sid)

@sio.event
async def start_game(sid, data):
    room_id = data.get("room_id")
    total_rounds = data.get("total_rounds", 1)
    if room_id not in game_state.rooms:
        return
    room = game_state.rooms[room_id]
    if not room["game_started"] and len(room["players"]) >= 2:
        room["game_started"] = True
        room["total_rounds"] = total_rounds
        room["current_round"] = 1
        # Deal 5 cards to each player.
        for pid in room["players"]:
            game_state.deal_cards(room_id, pid, count=5)
        # For the very first turn, the table is empty; the current player must draw.
        room["current_turn"] = list(room["players"].keys())[0]
        print(f"Game started in room {room_id}. Current turn: {room['current_turn']}")
        await sio.emit("game_started", {
            "players": [
                {"id": pid, "hand": room["players"][pid]["hand"], "score": room["players"][pid]["score"]}
                for pid in room["players"]
            ],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
            "current_round": room["current_round"]
        }, room=room_id)
    else:
        await sio.emit("error", {"message": "Not enough players or game already started."}, room=sid)

@sio.event
async def draw_card(sid, data):
    player_id = data.get("player_id")
    if player_id not in game_state.player_rooms:
        await sio.emit("error", {"message": "Player not in a room."}, room=sid)
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
    # Draw one card and mark that the player has drawn this turn.
    game_state.deal_cards(room_id, player_id, count=1)
    room["players"][player_id]["has_drawn"] = True
    print(f"Player {player_id} drew a card. New hand count: {len(room['players'][player_id]['hand'])}")
    await sio.emit("hand_updated", {
        "hand": room["players"][player_id]["hand"],
        "deck_count": len(room["deck"])
    }, room=sid)
    await sio.emit("deck_updated", {
        "deck_count": len(room["deck"])
    }, room=room_id)

@sio.event
async def play_cards(sid, data):
    player_id = data.get("player_id")
    card_indices = data.get("card_indices", [])
    if not player_id or not card_indices:
        await sio.emit("error", {"message": "Missing player_id or card_indices"}, room=sid)
        return
    room_id = game_state.player_rooms.get(player_id)
    if room_id and game_state.play_cards(player_id, card_indices):
        room = game_state.rooms[room_id]
        await sio.emit("cards_played", {
            "player_id": player_id,
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"])
        }, room=room_id)
        await sio.emit("hand_updated", {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        # Check for instant win: if player's hand is empty, award +4 points and end round.
        if not room["players"][player_id]["hand"]:
            room["players"][player_id]["score"] += 4
            await sio.emit("round_won", {
                "player_id": player_id,
                "score": room["players"][player_id]["score"],
                "deck_count": len(room["deck"])
            }, room=room_id)
            game_state.next_round(room_id, round_winner=player_id)
    else:
        await sio.emit("error", {"message": "Invalid play or move rejected."}, room=sid)

@sio.event
async def call(sid, data):
    player_id = data.get("player_id")
    if player_id in game_state.player_rooms:
        result = game_state.calculate_call_result(player_id)
        if result:
            room_id = game_state.player_rooms[player_id]
            await sio.emit("call_result", result, room=room_id)
        else:
            await sio.emit("error", {"message": "Call could not be processed."}, room=sid)

@app.get("/")
async def read_root():
    return {"message": "Card Game Backend"}

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
