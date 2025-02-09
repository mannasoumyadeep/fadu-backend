// src/WebSocketGame.js
import React, { useEffect, useState } from 'react';

const WebSocketGame = ({ roomId, userId }) => {
  const [socket, setSocket] = useState(null);
  const [privateState, setPrivateState] = useState({});
  const [publicMessages, setPublicMessages] = useState([]);

  useEffect(() => {
    // For local testing use ws://localhost:8000; later change this to your deployed backend URL.
    const ws = new WebSocket(`ws://localhost:8000/ws/${roomId}/${userId}`);
    
    ws.onopen = () => {
      console.log('Connected to WebSocket server');
    };
    
    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log("Received:", data);
      
      if (data.type === "private_update") {
        setPrivateState(data);
      } else {
        setPublicMessages((prev) => [...prev, data.message]);
      }
    };
    
    ws.onclose = () => {
      console.log('WebSocket connection closed');
    };

    setSocket(ws);

    // Cleanup on component unmount
    return () => {
      ws.close();
    };
  }, [roomId, userId]);

  const sendAction = (action) => {
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ action }));
    }
  };

  return (
    <div className="p-4">
      <h2 className="text-xl font-bold">Room: {roomId}</h2>
      <p>User: {userId}</p>
      
      <div className="my-4">
        <h3 className="text-lg">Your Hand:</h3>
        {privateState.hand ? (
          <ul className="list-disc pl-5">
            {privateState.hand.map((card, idx) => (
              <li key={idx}>{card}</li>
            ))}
          </ul>
        ) : (
          <p>Loading hand...</p>
        )}
      </div>
      
      <div className="my-4">
        <h3 className="text-lg">Public Updates:</h3>
        <ul className="list-disc pl-5">
          {publicMessages.map((msg, idx) => (
            <li key={idx}>{msg}</li>
          ))}
        </ul>
      </div>
      
      <button 
        className="px-4 py-2 bg-blue-500 text-white rounded"
        onClick={() => sendAction("draw_card")}
      >
        Draw Card
      </button>
    </div>
  );
};

export default WebSocketGame;
