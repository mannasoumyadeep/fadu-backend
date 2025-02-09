#!/bin/bash
python -c "import os; port = int(os.getenv('PORT', '8080')); from main import app; import uvicorn; uvicorn.run(app, host='0.0.0.0', port=port)"