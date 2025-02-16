import os
import random
import socketio
import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime, timedelta

app = FastAPI()
sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins=["*"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class GameState:
    def __init__(self):
        self.rooms = {}           # room_code -> room details
        self.player_rooms = {}    # player_id -> room_code
        self.player_sids = {}     # player_id -> socket_id
        self.disconnect_times = {} # player_id -> disconnect_timestamp

    def create_room(self, room_code):
        self.rooms[room_code] = {
            "players": {},        # player_id -> { hand, score, has_drawn, connected }
            "deck": self.initialize_deck(),
            "table_cards": [],
            "current_turn": None,
            "host_id": None,
            "game_started": False,
            "total_rounds": 1,
            "current_round": 1,
            "game_paused": False,
            "player_order": [],   # Maintains turn order
        }
        return self.rooms[room_code]

    def initialize_deck(self):
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))
        deck = [{"suit": s, "value": v} for s in suits for v in values]
        random.shuffle(deck)
        return deck

    def add_player(self, room_code, player_id, sid):
        if len(self.rooms[room_code]["players"]) >= 8:
            raise ValueError("Room is full (max 8 players)")
        
        # Check for duplicate player names
        if player_id in self.rooms[room_code]["players"]:
            raise ValueError("Player name already exists in room")

        room = self.rooms[room_code]
        is_host = len(room["players"]) == 0
        
        room["players"][player_id] = {
            "hand": [],
            "score": 0,
            "has_drawn": False,
            "connected": True
        }
        
        if is_host:
            room["host_id"] = player_id
            room["player_order"].append(player_id)
        else:
            room["player_order"].append(player_id)
            
        self.player_rooms[player_id] = room_code
        self.player_sids[player_id] = sid
        
        return room, is_host

    def deal_cards(self, room_code, player_id, count=5):
        room = self.rooms[room_code]
        player = room["players"][player_id]
        
        # Check hand limit
        if len(player["hand"]) + count > 6:
            return False, "Hand size would exceed limit"
            
        for _ in range(count):
            if not room["deck"]:
                if not self.reshuffle(room_code):
                    return False, "No cards available"
            player["hand"].append(room["deck"].pop())
            
        return True, player["hand"]

    def reshuffle(self, room_code):
        room = self.rooms[room_code]
        if len(room["table_cards"]) <= 1:
            return False
            
        top = room["table_cards"][-1]
        rest = room["table_cards"][:-1]
        random.shuffle(rest)
        room["deck"].extend(rest)
        room["table_cards"] = [top]
        return True

    def can_play(self, room_code, player_id, indices):
        room = self.rooms[room_code]
        player = room["players"][player_id]
        hand = player["hand"]
        
        # Basic validations
        if not all(0 <= i < len(hand) for i in indices):
            return False, "Invalid card indices"
            
        if len(hand) - len(indices) + (0 if player["has_drawn"] else 1) > 5:
            return False, "Would exceed hand limit"
            
        selected = [hand[i] for i in indices]
        
        # First play of the game
        if not room["table_cards"]:
            if not player["has_drawn"]:
                return False, "Must draw first"
            if len(selected) != 1:
                return False, "Must play exactly one card"
            return True, None
            
        # Regular play
        top_val = room["table_cards"][-1]["value"]
        has_match = any(card["value"] == top_val for card in hand)
        
        if has_match:
            if not player["has_drawn"]:
                # Can play multiple matching cards
                if not all(card["value"] == top_val for card in selected):
                    return False, "All cards must match top card"
                return True, None
        
        # Must have drawn and play exactly one card
        if not player["has_drawn"]:
            return False, "Must draw first"
        if len(selected) != 1:
            return False, "Must play exactly one card"
            
        return True, None

    def play_cards(self, player_id, indices):
        room_code = self.player_rooms[player_id]
        room = self.rooms[room_code]
        player = room["players"][player_id]
        
        can_play, error = self.can_play(room_code, player_id, indices)
        if not can_play:
            return False, error
            
        # Play the cards
        for i in sorted(indices, reverse=True):
            card = player["hand"].pop(i)
            room["table_cards"].append(card)
            
        player["has_drawn"] = False
        
        # Advance turn
        current_idx = room["player_order"].index(player_id)
        room["current_turn"] = room["player_order"][(current_idx + 1) % len(room["player_order"])]
        
        return True, None

    def calculate_call(self, caller_id):
        room_code = self.player_rooms[caller_id]
        room = self.rooms[room_code]
        
        # Calculate hand sums
        sums = {pid: sum(card["value"] for card in p["hand"]) 
                for pid, p in room["players"].items()}
        
        caller_sum = sums[caller_id]
        low = min(sums.values())
        winners = [pid for pid, s in sums.items() if s == low]
        
        # Scoring
        if caller_sum == low and len(winners) == 1:
            room["players"][caller_id]["score"] += 3
            result = "win"
        else:
            room["players"][caller_id]["score"] -= 2
            for pid in winners:
                room["players"][pid]["score"] += 1
            result = "loss"
            
        return {
            "result": result,
            "scores": {pid: p["score"] for pid, p in room["players"].items()},
            "player_sums": sums
        }

    def disconnect_player(self, player_id):
        if player_id in self.player_rooms:
            room_code = self.player_rooms[player_id]
            room = self.rooms[room_code]
            room["players"][player_id]["connected"] = False
            room["game_paused"] = True
            self.disconnect_times[player_id] = datetime.now()
            return room_code
        return None

    def reconnect_player(self, player_id, sid):
        if player_id in self.player_rooms:
            room_code = self.player_rooms[player_id]
            room = self.rooms[room_code]
            room["players"][player_id]["connected"] = True
            self.player_sids[player_id] = sid
            if all(p["connected"] for p in room["players"].values()):
                room["game_paused"] = False
            if player_id in self.disconnect_times:
                del self.disconnect_times[player_id]
            return room_code
        return None

    async def check_disconnections(self):
        while True:
            current_time = datetime.now()
            for player_id, disconnect_time in list(self.disconnect_times.items()):
                if current_time - disconnect_time > timedelta(minutes=2):
                    # Forfeit the game
                    if player_id in self.player_rooms:
                        room_code = self.player_rooms[player_id]
                        await sio.emit("game_forfeited", {
                            "player_id": player_id,
                            "reason": "Player disconnected for too long"
                        }, room=room_code)
                        # Clean up player data
                        del self.rooms[room_code]
                        del self.player_rooms[player_id]
                        del self.disconnect_times[player_id]
            await asyncio.sleep(10)  # Check every 10 seconds

game_state = GameState()

# Start the disconnection checker
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(game_state.check_disconnections())

# Socket.IO Event Handlers
@sio.event
async def connect(sid, environ):
    print(f"Client connected: {sid}")

@sio.event
async def disconnect(sid):
    # Find player by sid and handle disconnection
    for player_id, player_sid in game_state.player_sids.items():
        if player_sid == sid:
            room_code = game_state.disconnect_player(player_id)
            if room_code:
                await sio.emit("player_disconnected", {
                    "player_id": player_id,
                    "game_paused": True
                }, room=room_code)
            break

@sio.event
async def join_room(sid, data):
    try:
        room_code = data.get("room_id")
        player_id = data.get("player_id")
        
        if not room_code or not player_id:
            raise ValueError("Missing room_id or player_id")
            
        if not 2 <= len(game_state.rooms[room_code]["players"]) <= 8:
            raise ValueError("Room must have 2-8 players")
            
        room, is_host = game_state.add_player(room_code, player_id, sid)
        sio.enter_room(sid, room_code)
        
        # Send game state to the joining player
        await sio.emit("game_state", {
            "hand": room["players"][player_id]["hand"],
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "players": [{"id": pid, "score": p["score"], "card_count": len(p["hand"])} 
                       for pid, p in room["players"].items()],
            "host_id": room["host_id"],
            "deck_count": len(room["deck"]),
            "game_status": "waiting" if not room["game_started"] else "playing",
            "current_round": room["current_round"],
            "game_paused": room["game_paused"]
        }, room=sid)
        
        # Notify others
        await sio.emit("player_joined", {
            "player_id": player_id,
            "is_host": is_host,
            "players": list(room["players"].keys())
        }, room=room_code)
        
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, room=sid)

@sio.event
async def start_game(sid, data):
    try:
        room_code = data.get("room_id")
        total_rounds = data.get("total_rounds", 1)
        
        if room_code not in game_state.rooms:
            raise ValueError("Room not found")
            
        room = game_state.rooms[room_code]
        if room["game_started"]:
            raise ValueError("Game already started")
            
        if len(room["players"]) < 2:
            raise ValueError("Not enough players")
            
        if len(room["players"]) > 8:
            raise ValueError("Too many players")
            
        # Initialize game
        room["game_started"] = True
        room["total_rounds"] = total_rounds
        room["current_round"] = 1
        
        # Deal initial cards
        for pid in room["players"]:
            success, result = game_state.deal_cards(room_code, pid, count=5)
            if not success:
                raise ValueError(f"Failed to deal cards: {result}")
                
        # Set first turn
        room["current_turn"] = room["player_order"][0]
        
        # Notify all players
        await sio.emit("game_started", {
            "players": [{
                "id": pid,
                "score": p["score"],
                "card_count": len(p["hand"])
            } for pid, p in room["players"].items()],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
            "current_round": room["current_round"],
            "total_rounds": room["total_rounds"]
        }, room=room_code)
        
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, room=sid)

@sio.event
async def draw_card(sid, data):
    try:
        player_id = data.get("player_id")
        if player_id not in game_state.player_rooms:
            raise ValueError("Player not in room")
            
        room_code = game_state.player_rooms[player_id]
        room = game_state.rooms[room_code]
        
        if room["game_paused"]:
            raise ValueError("Game is paused")
            
        if room["current_turn"] != player_id:
            raise ValueError("Not your turn")
            
        if room["players"][player_id]["has_drawn"]:
            raise ValueError("Already drawn this turn")
            
        # Check if player must play matching card instead of drawing
        top_card = room["table_cards"][-1] if room["table_cards"] else None
        if top_card:
            has_match = any(card["value"] == top_card["value"] 
                          for card in room["players"][player_id]["hand"])
            if has_match and not room["players"][player_id]["has_drawn"]:
                raise ValueError("Must play matching card")
                
        # Draw card
        success, result = game_state.deal_cards(room_code, player_id, count=1)
        if not success:
            raise ValueError(result)
            
        room["players"][player_id]["has_drawn"] = True
        
        # Notify player of their new hand
        await sio.emit("hand_updated", {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        
        # Notify others of deck count and card counts
        await sio.emit("player_state_updated", {
            "player_id": player_id,
            "card_count": len(room["players"][player_id]["hand"]),
            "deck_count": len(room["deck"])
        }, room=room_code)
        
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, room=sid)

@sio.event
async def play_cards(sid, data):
    try:
        player_id = data.get("player_id")
        indices = data.get("card_indices", [])
        
        if not player_id or not indices:
            raise ValueError("Missing parameters")
            
        if player_id not in game_state.player_rooms:
            raise ValueError("Player not in room")
            
        room_code = game_state.player_rooms[player_id]
        room = game_state.rooms[room_code]
        
        if room["game_paused"]:
            raise ValueError("Game is paused")
            
        if room["current_turn"] != player_id:
            raise ValueError("Not your turn")
            
        # Attempt to play cards
        success, error = game_state.play_cards(player_id, indices)
        if not success:
            raise ValueError(error)
            
        # Check if player won the round
        if not room["players"][player_id]["hand"]:
            room["players"][player_id]["score"] += 4
            await sio.emit("round_won", {
                "player_id": player_id,
                "winning_type": "empty_hand",
                "score": room["players"][player_id]["score"]
            }, room=room_code)
            
            # Start next round
            await start_next_round(room_code, round_winner=player_id)
            return
            
        # Notify all players of the play
        await sio.emit("cards_played", {
            "player_id": player_id,
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"]),
            "player_card_counts": {
                pid: len(p["hand"]) for pid, p in room["players"].items()
            }
        }, room=room_code)
        
        # Update player's hand
        await sio.emit("hand_updated", {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, room=sid)

@sio.event
async def call(sid, data):
    try:
        player_id = data.get("player_id")
        if not player_id:
            raise ValueError("Missing player_id")
            
        if player_id not in game_state.player_rooms:
            raise ValueError("Player not in room")
            
        room_code = game_state.player_rooms[player_id]
        room = game_state.rooms[room_code]
        
        if room["game_paused"]:
            raise ValueError("Game is paused")
            
        if room["current_turn"] != player_id:
            raise ValueError("Not your turn")
            
        if room["players"][player_id]["has_drawn"]:
            raise ValueError("Cannot call after drawing")
            
        # Calculate call results
        result = game_state.calculate_call(player_id)
        await sio.emit("call_result", result, room=room_code)
        
        # Start next round
        await start_next_round(room_code, round_winner=None)
        
    except Exception as e:
        await sio.emit("error", {"message": str(e)}, room=sid)

async def start_next_round(room_code, round_winner=None):
    room = game_state.rooms[room_code]
    room["current_round"] += 1
    
    # Check if game is over
    if room["current_round"] > room["total_rounds"]:
        # Calculate final results
        scores = {pid: p["score"] for pid, p in room["players"].items()}
        high_score = max(scores.values())
        winners = [pid for pid, score in scores.items() if score == high_score]
        
        await sio.emit("game_over", {
            "scores": scores,
            "winners": winners
        }, room=room_code)
        return
        
    # Reset for next round
    room["deck"] = game_state.initialize_deck()
    room["table_cards"] = []
    
    for pid in room["players"]:
        room["players"][pid]["hand"] = []
        room["players"][pid]["has_drawn"] = False
        game_state.deal_cards(room_code, pid, count=5)
        
    # Set first turn for new round
    if round_winner and round_winner in room["players"]:
        room["current_turn"] = round_winner
    else:
        room["current_turn"] = room["player_order"][0]
        
    # Notify players of new round
    await sio.emit("round_started", {
        "current_round": room["current_round"],
        "current_turn": room["current_turn"],
        "deck_count": len(room["deck"]),
        "player_card_counts": {
            pid: len(p["hand"]) for pid, p in room["players"].items()
        }
    }, room=room_code)

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)