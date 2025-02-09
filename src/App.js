import React, { useState, useEffect } from 'react';
import WebSocketGame from './WebSocketGame'; // (Note: Although imported, this component is not used directly in the render below.)
import './styles.css';

function App() {
  // Game state variables
  const [gameStarted, setGameStarted] = useState(false);
  const [numPlayers, setNumPlayers] = useState(2);
  const [numRounds, setNumRounds] = useState(5);
  const [currentRound, setCurrentRound] = useState(1);
  const [players, setPlayers] = useState([]);
  const [currentPlayer, setCurrentPlayer] = useState(0);
  const [deck, setDeck] = useState([]);
  const [tableCard, setTableCard] = useState(null);
  const [selectedCard, setSelectedCard] = useState(null);
  const [showWinner, setShowWinner] = useState(false);
  const [gameWinners, setGameWinners] = useState([]);
  const [hasDrawn, setHasDrawn] = useState(false);

  // WebSocket state
  // For now, we use static values; you can later prompt the user for these.
  const roomId = "room1";
  const userId = "player1";
  const [socket, setSocket] = useState(null);

  // Establish WebSocket connection on mount
  useEffect(() => {
    // Change the URL when deploying your backend (and use "wss://" for secure connections)
    const ws = new WebSocket(`ws://localhost:8000/ws/${roomId}/${userId}`);
    
    ws.onopen = () => {
      console.log('Connected to WebSocket server');
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log("Received:", data);
      
      if (data.type === "welcome") {
        // For example, log the welcome message
        console.log(data.message);
      } else if (data.type === "private_update") {
        // Update the player's hand using the players state.
        setPlayers(prevPlayers =>
          prevPlayers.map(player =>
            player.id === userId ? { ...player, hand: data.hand } : player
          )
        );
      } else if (data.type === "public_update") {
        // Update public game state, such as the table card.
        if (data.tableCard) {
          setTableCard(data.tableCard);
        }
        console.log("Broadcast:", data.message);
      } else if (data.type === "broadcast") {
        // Optionally handle generic broadcast messages.
        console.log("Broadcast:", data.message);
      }
    };
    
    ws.onerror = (error) => {
      console.error("WebSocket error:", error);
    };
    
    ws.onclose = () => {
      console.log("WebSocket connection closed");
    };
    
    setSocket(ws);
    
    // Clean up on component unmount
    return () => {
      ws.close();
    };
  }, [roomId, userId]);

  // Initialize a new deck of cards
  const initializeDeck = () => {
    const suits = ['Hearts', 'Diamonds', 'Clubs', 'Spades'];
    const values = Array.from({ length: 13 }, (_, i) => i + 1);
    let newDeck = [];
    for (const suit of suits) {
      for (const value of values) {
        newDeck.push({ suit, value });
      }
    }
    return newDeck.sort(() => Math.random() - 0.5);
  };

  // Reshuffle the deck
  const reshuffleDeck = () => {
    const newDeck = initializeDeck();
    setDeck(newDeck);
  };

  // Trigger drawing a card by sending an action via WebSocket
  const drawCard = () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "draw_card" }));
      setHasDrawn(true);
    } else {
      console.error("WebSocket is not open");
    }
  };

  // Trigger playing a card by sending the selected card index via WebSocket
  const playCard = () => {
    if (selectedCard === null) return;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "play_card", cardIndex: selectedCard }));
    } else {
      console.error("WebSocket is not open");
    }
  };

  // Trigger the call action via WebSocket
  const handleCall = () => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action: "call" }));
    }
  };

  // Local functions to manage rounds and scoring
  const handlePlayerWin = () => {
    const newPlayers = [...players];
    newPlayers[currentPlayer].score += 4; // Bonus for playing all cards
    setPlayers(newPlayers);
    startNewRound();
  };

  const startNewRound = () => {
    setCurrentRound(prev => prev + 1);
    if (currentRound >= numRounds) {
      endGame();
      return;
    }
    const newDeck = initializeDeck();
    const newPlayers = players.map(player => ({ ...player, hand: [] }));
    // Deal 5 cards to each player
    for (let i = 0; i < 5; i++) {
      for (let player of newPlayers) {
        player.hand.push(newDeck.pop());
      }
    }
    setDeck(newDeck);
    setPlayers(newPlayers);
    setTableCard(newDeck.pop());
    setCurrentPlayer(0);
    setHasDrawn(false);
  };

  const endGame = () => {
    const maxScore = Math.max(...players.map(p => p.score));
    const winners = players.filter(p => p.score === maxScore);
    setGameWinners(winners);
    setShowWinner(true);
  };

  const handleStartGame = () => {
    const newDeck = initializeDeck();
    // Create players with a sample hand and a score of 0.
    const newPlayers = Array.from({ length: numPlayers }, (_, i) => ({
      id: i === 0 ? userId : `player${i + 1}`,
      name: `Player ${i + 1}`,
      hand: [],
      score: 0,
    }));
    // Deal initial 5 cards to each player
    for (let i = 0; i < 5; i++) {
      for (let player of newPlayers) {
        player.hand.push(newDeck.pop());
      }
    }
    setDeck(newDeck);
    setPlayers(newPlayers);
    setTableCard(newDeck.pop());
    setCurrentRound(1);
    setGameStarted(true);
    setCurrentPlayer(0);
    setHasDrawn(false);
  };

  const resetGame = () => {
    setGameStarted(false);
    setShowWinner(false);
    setGameWinners([]);
    setPlayers([]);
    setDeck([]);
    setTableCard(null);
    setSelectedCard(null);
    setCurrentPlayer(0);
    setCurrentRound(1);
    setHasDrawn(false);
  };

  // If the game hasn't started, show the setup screen.
  if (!gameStarted) {
    return (
      <div className="game-container">
        <div className="setup-form">
          <h1 className="title">Fadu Card Game</h1>
          <div className="input-group">
            <label>Number of Players:</label>
            <input
              type="number"
              min="2"
              max="8"
              value={numPlayers}
              onChange={(e) => setNumPlayers(parseInt(e.target.value))}
            />
          </div>
          <div className="input-group">
            <label>Number of Rounds:</label>
            <input
              type="number"
              min="1"
              value={numRounds}
              onChange={(e) => setNumRounds(parseInt(e.target.value))}
            />
          </div>
          <button className="start-button" onClick={handleStartGame}>
            Start Game
          </button>
        </div>
      </div>
    );
  }

  // Main game interface
  return (
    <div className="game-container">
      <div className="game-board">
        <div className="round-info">
          Round {currentRound} of {numRounds}
        </div>
        <h1 className="title">Current Player: {players[currentPlayer]?.name}</h1>
        <div className="scores-container">
          {players.map(player => (
            <div key={player.id} className="player-score">
              {player.name}: {player.score} points
            </div>
          ))}
        </div>
        <div className="table-area">
          {tableCard && (
            <div className="card">
              {tableCard.value} of {tableCard.suit}
            </div>
          )}
          <div className="deck-area">
            <div className="deck-card">
              Deck: {deck.length} cards
            </div>
          </div>
        </div>
        <div className="player-hand">
          <h2>Your Cards:</h2>
          <div className="cards-container">
            {players[currentPlayer]?.hand.map((card, index) => (
              <div
                key={index}
                className={`card ${selectedCard === index ? 'selected' : ''}`}
                onClick={() => setSelectedCard(index)}
              >
                {card.value} of {card.suit}
              </div>
            ))}
          </div>
        </div>
        <div className="controls">
          <button
            className="game-button"
            onClick={drawCard}
            disabled={hasDrawn || (tableCard && players[currentPlayer]?.hand.some(
              card => card.value === tableCard.value
            ))}
          >
            Draw Card
          </button>
          <button
            className="game-button"
            onClick={playCard}
            disabled={selectedCard === null}
          >
            Play Card
          </button>
          <button className="game-button" onClick={handleCall}>
            Call
          </button>
        </div>
      </div>
      {showWinner && (
        <>
          <div className="overlay"></div>
          <div className="winner-announcement">
            <h2>Game Over!</h2>
            {gameWinners.length === 1 ? (
              <p>{gameWinners[0].name} wins with {gameWinners[0].score} points!</p>
            ) : (
              <div>
                <p>It's a tie between:</p>
                {gameWinners.map(winner => (
                  <p key={winner.id}>
                    {winner.name} with {winner.score} points
                  </p>
                ))}
              </div>
            )}
            <button className="start-button" onClick={resetGame}>
              Play Again
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export default App;
