import os
import random
import socketio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
                "host_id": host_id
            }
        return self.rooms[room_id]

    def initialize_deck(self):
        suits = ["hearts", "diamonds", "clubs", "spades"]
        values = list(range(1, 14))  # 1 (Ace) to 13 (King)
        deck = [{"suit": suit, "value": value} for suit in suits for value in values]
        random.shuffle(deck)
        return deck

    def reshuffle_table_cards(self, room_id):
        room = self.rooms[room_id]
        if len(room["table_cards"]) <= 1:
            return False
        
        # Keep the top card and reshuffle the rest into the deck
        top_card = room["table_cards"][-1]
        cards_to_shuffle = room["table_cards"][:-1]
        random.shuffle(cards_to_shuffle)
        room["deck"].extend(cards_to_shuffle)
        room["table_cards"] = [top_card]
        return True

    def add_player(self, room_id, player_id, is_host=False):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if len(room["players"]) < room["max_players"]:
                room["players"][player_id] = {
                    "hand": [],
                    "score": 0,
                    "is_host": is_host,
                    "can_play_any": False  # flag set after drawing a card
                }
                self.player_rooms[player_id] = room_id
                
                if room["game_started"]:
                    self.deal_initial_cards(room_id, player_id)
                
                if room["current_turn"] is None and room["game_started"]:
                    room["current_turn"] = player_id
                
                return True
        return False

    def deal_initial_cards(self, room_id, player_id, count=5):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        for _ in range(count):
            if not room["deck"]:
                if not self.reshuffle_table_cards(room_id):
                    break
            if room["deck"]:
                player["hand"].append(room["deck"].pop())

    def can_play_cards(self, room_id, player_id, card_indices):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        
        # Validate indices
        if not all(0 <= idx < len(player["hand"]) for idx in card_indices):
            return False
        
        selected_cards = [player["hand"][idx] for idx in card_indices]
        
        # If the player has drawn a card this turn, allow playing any card.
        if player.get("can_play_any", False):
            return True
        
        # If table is empty (start of game), allow any card.
        if not room["table_cards"]:
            return True
        
        # Otherwise, enforce that played cards must match the top card’s value.
        top_card = room["table_cards"][-1]
        first_value = selected_cards[0]["value"]
        return all(card["value"] == first_value for card in selected_cards) and (first_value == top_card["value"])

    def play_cards(self, player_id, card_indices):
        if player_id in self.player_rooms:
            room_id = self.player_rooms[player_id]
            room = self.rooms[room_id]
            player = room["players"][player_id]
            
            if not self.can_play_cards(room_id, player_id, card_indices):
                return False
            
            # Remove cards from hand (largest indices first) and add to table.
            for idx in sorted(card_indices, reverse=True):
                card = player["hand"].pop(idx)
                room["table_cards"].append(card)
            
            # Reset drawn-card flag after playing.
            player["can_play_any"] = False
            
            # Advance turn.
            players = list(room["players"].keys())
            current_index = players.index(player_id)
            next_index = (current_index + 1) % len(players)
            room["current_turn"] = players[next_index]
            
            return True
        return False

    def calculate_call_result(self, caller_id):
        if caller_id not in self.player_rooms:
            return None
        
        room_id = self.player_rooms[caller_id]
        room = self.rooms[room_id]
        
        # Sum card values in each player's hand.
        player_sums = {}
        for pid, player in room["players"].items():
            player_sums[pid] = sum(card["value"] for card in player["hand"])
        
        caller_sum = player_sums[caller_id]
        lowest_sum = min(player_sums.values())
        winners = [pid for pid, total in player_sums.items() if total == lowest_sum]
        
        # Update scores per new rules.
        if caller_sum == lowest_sum and len(winners) == 1:
            room["players"][caller_id]["score"] += 3  # caller gets +3 points
            result = "win"
        else:
            room["players"][caller_id]["score"] -= 2  # caller gets -2 points
            for pid in winners:
                if pid != caller_id:
                    room["players"][pid]["score"] += 2  # others get +2 points
            result = "loss"
        
        return {
            "result": result,
            "scores": {pid: info["score"] for pid, info in room["players"].items()},
            "player_sums": player_sums
        }

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
    is_host = data.get('is_host', False)
    
    if not room_id or not player_id:
        await sio.emit('error', {"message": "Missing room_id or player_id"}, room=sid)
        return
    
    room = game_state.create_room(room_id, player_id if is_host else None)
    if game_state.add_player(room_id, player_id, is_host):
        sio.enter_room(sid, room_id)
        await sio.emit('game_state', {
            "hand": room["players"][player_id]["hand"],
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "players": list(room["players"].keys()),
            "is_host": is_host,
            "deck_count": len(room["deck"])
        }, room=sid)
        
        await sio.emit('player_joined', {
            "player_id": player_id,
            "players": list(room["players"].keys()),
            "host_id": room["host_id"]
        }, room=room_id)

@sio.event
async def start_game(sid, data):
    room_id = data.get('room_id')
    if room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        if not room["game_started"] and len(room["players"]) >= 2:
            room["game_started"] = True
            
            # Deal 5 cards to each player.
            for player_id in room["players"]:
                game_state.deal_initial_cards(room_id, player_id)
            
            # Set the first player as current turn.
            room["current_turn"] = list(room["players"].keys())[0]
            
            await sio.emit('game_started', {
                "players": [
                    {
                        "id": pid,
                        "hand": room["players"][pid]["hand"],
                        "score": room["players"][pid]["score"]
                    }
                    for pid in room["players"]
                ],
                "current_turn": room["current_turn"],
                "deck_count": len(room["deck"])
            }, room=room_id)

@sio.event
async def play_cards(sid, data):
    player_id = data.get('player_id')
    card_indices = data.get('card_indices', [])
    
    if not player_id or not card_indices:
        return
    
    room_id = game_state.player_rooms.get(player_id)
    if room_id and game_state.play_cards(player_id, card_indices):
        room = game_state.rooms[room_id]
        await sio.emit('cards_played', {
            "player_id": player_id,
            "table_cards": room["table_cards"],
            "current_turn": room["current_turn"],
            "deck_count": len(room["deck"])
        }, room=room_id)
        
        # Update the player's hand.
        await sio.emit('hand_updated', {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        
        # Check for an instant win (if the player has played all cards).
        if not room["players"][player_id]["hand"]:
            room["players"][player_id]["score"] += 4  # +4 points for instant win
            await sio.emit('round_won', {
                "player_id": player_id,
                "score": room["players"][player_id]["score"],
                "deck_count": len(room["deck"])
            }, room=room_id)

@sio.event
async def draw_card(sid, data):
    player_id = data.get('player_id')
    if player_id in game_state.player_rooms:
        room_id = game_state.player_rooms[player_id]
        room = game_state.rooms[room_id]
        
        if room["current_turn"] == player_id:
            if not room["deck"]:
                if game_state.reshuffle_table_cards(room_id):
                    await sio.emit('deck_reshuffled', {
                        "deck_count": len(room["deck"]),
                        "top_card": room["table_cards"][-1] if room["table_cards"] else None
                    }, room=room_id)
            
            if room["deck"]:
                # Draw one card.
                game_state.deal_initial_cards(room_id, player_id, count=1)
                # Allow player to play any card after drawing.
                room["players"][player_id]["can_play_any"] = True
                
                await sio.emit('hand_updated', {
                    "hand": room["players"][player_id]["hand"],
                    "deck_count": len(room["deck"])
                }, room=sid)
                
                await sio.emit('deck_updated', {
                    "deck_count": len(room["deck"])
                }, room=room_id)

@sio.event
async def call(sid, data):
    player_id = data.get('player_id')
    if player_id in game_state.player_rooms:
        result = game_state.calculate_call_result(player_id)
        if result:
            room_id = game_state.player_rooms[player_id]
            await sio.emit('call_result', result, room=room_id)

@app.get("/")
async def read_root():
    return {"message": "Card Game Backend"}

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
