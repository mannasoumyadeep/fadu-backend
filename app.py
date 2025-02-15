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
        # Keep the top card; reshuffle the rest into the deck.
        top_card = room["table_cards"][-1]
        cards_to_shuffle = room["table_cards"][:-1]
        random.shuffle(cards_to_shuffle)
        room["deck"].extend(cards_to_shuffle)
        room["table_cards"] = [top_card]
        print(f"Reshuffled table cards into deck; new deck count: {len(room['deck'])}")
        return True

    def add_player(self, room_id, player_id, is_host=False):
        if room_id in self.rooms:
            room = self.rooms[room_id]
            if len(room["players"]) < room["max_players"]:
                room["players"][player_id] = {
                    "hand": [],
                    "score": 0,
                    "is_host": is_host,
                    "can_play_any": False  # Flag: becomes True after drawing when needed
                }
                self.player_rooms[player_id] = room_id
                # If game already started, deal new player 5 cards.
                if room["game_started"]:
                    self.deal_cards(room_id, player_id, count=5)
                # If game started and no current turn set, set it.
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
        print(f"Dealt {count} cards to {player_id}. Hand count: {len(player['hand'])}")

    def can_play_cards(self, room_id, player_id, card_indices):
        room = self.rooms[room_id]
        player = room["players"][player_id]
        # Validate indices
        if not all(0 <= idx < len(player["hand"]) for idx in card_indices):
            return False
        
        selected_cards = [player["hand"][idx] for idx in card_indices]
        # If table is empty, allow any play.
        if not room["table_cards"]:
            return True
        
        top_card = room["table_cards"][-1]
        # Check if player has any card matching the top card's value in their hand.
        has_match_in_hand = any(card["value"] == top_card["value"] for card in player["hand"])
        
        if has_match_in_hand:
            # If player has a matching card, they must play cards matching the top card.
            return all(card["value"] == top_card["value"] for card in selected_cards)
        else:
            # If no matching card, player must have drawn a card (flag is set) to allow any play.
            return player.get("can_play_any", False)

    def play_cards(self, player_id, card_indices):
        if player_id in self.player_rooms:
            room_id = self.player_rooms[player_id]
            room = self.rooms[room_id]
            player = room["players"][player_id]
            if not self.can_play_cards(room_id, player_id, card_indices):
                print(f"Player {player_id} cannot play selected cards: {card_indices}")
                return False
            # Remove cards from hand (largest indices first) and add them to table.
            for idx in sorted(card_indices, reverse=True):
                card = player["hand"].pop(idx)
                room["table_cards"].append(card)
            # Reset the flag after playing.
            player["can_play_any"] = False
            print(f"Player {player_id} played cards. New hand count: {len(player['hand'])}")
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
        # Calculate the sum of card values for each player's hand.
        player_sums = {}
        for pid, player in room["players"].items():
            player_sums[pid] = sum(card["value"] for card in player["hand"])
        caller_sum = player_sums[caller_id]
        lowest_sum = min(player_sums.values())
        winners = [pid for pid, total in player_sums.items() if total == lowest_sum]
        # Update scores based on the new rules.
        if caller_sum == lowest_sum and len(winners) == 1:
            room["players"][caller_id]["score"] += 3
            result = "win"
        else:
            room["players"][caller_id]["score"] -= 2
            for pid in winners:
                if pid != caller_id:
                    room["players"][pid]["score"] += 2
            result = "loss"
        print(f"Call result calculated for {caller_id}: {result}. Sums: {player_sums}")
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
    else:
        await sio.emit('error', {"message": "Unable to join room."}, room=sid)

@sio.event
async def start_game(sid, data):
    room_id = data.get('room_id')
    if room_id in game_state.rooms:
        room = game_state.rooms[room_id]
        if not room["game_started"] and len(room["players"]) >= 2:
            room["game_started"] = True
            # Deal 5 cards to each player.
            for player_id in room["players"]:
                game_state.deal_cards(room_id, player_id, count=5)
            # Set the first player as the current turn.
            room["current_turn"] = list(room["players"].keys())[0]
            print(f"Game started in room {room_id}. Current turn: {room['current_turn']}")
            await sio.emit('game_started', {
                "players": [
                    {
                        "id": pid,
                        "hand": room["players"][pid]["hand"],
                        "score": room["players"][pid]["score"]
                    } for pid in room["players"]
                ],
                "current_turn": room["current_turn"],
                "deck_count": len(room["deck"])
            }, room=room_id)
        else:
            await sio.emit('error', {"message": "Not enough players to start or game already started."}, room=sid)

@sio.event
async def draw_card(sid, data):
    player_id = data.get('player_id')
    if player_id in game_state.player_rooms:
        room_id = game_state.player_rooms[player_id]
        room = game_state.rooms[room_id]
        if room["current_turn"] != player_id:
            await sio.emit('error', {"message": "Not your turn to draw."}, room=sid)
            return
        if not room["deck"]:
            if not game_state.reshuffle_table_cards(room_id):
                await sio.emit('error', {"message": "Deck is empty and cannot be reshuffled."}, room=sid)
                return
        # Draw one card.
        game_state.deal_cards(room_id, player_id, count=1)
        # Set flag so that after drawing, the player may play any card.
        room["players"][player_id]["can_play_any"] = True
        print(f"Player {player_id} drew a card. New hand count: {len(room['players'][player_id]['hand'])}")
        await sio.emit('hand_updated', {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        await sio.emit('deck_updated', {
            "deck_count": len(room["deck"])
        }, room=room_id)

@sio.event
async def play_cards(sid, data):
    player_id = data.get('player_id')
    card_indices = data.get('card_indices', [])
    if not player_id or not card_indices:
        await sio.emit('error', {"message": "Missing player_id or card_indices"}, room=sid)
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
        await sio.emit('hand_updated', {
            "hand": room["players"][player_id]["hand"],
            "deck_count": len(room["deck"])
        }, room=sid)
        # Check for instant win: if player’s hand is now empty.
        if not room["players"][player_id]["hand"]:
            room["players"][player_id]["score"] += 4
            await sio.emit('round_won', {
                "player_id": player_id,
                "score": room["players"][player_id]["score"],
                "deck_count": len(room["deck"])
            }, room=room_id)
    else:
        await sio.emit('error', {"message": "Invalid play or move rejected."}, room=sid)

@sio.event
async def call(sid, data):
    player_id = data.get('player_id')
    if player_id in game_state.player_rooms:
        result = game_state.calculate_call_result(player_id)
        if result:
            room_id = game_state.player_rooms[player_id]
            await sio.emit('call_result', result, room=room_id)
        else:
            await sio.emit('error', {"message": "Call could not be processed."}, room=sid)

@app.get("/")
async def read_root():
    return {"message": "Card Game Backend"}

socket_app = socketio.ASGIApp(sio, app)
app = socket_app

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
