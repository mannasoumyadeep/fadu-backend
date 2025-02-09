FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD python -c "import os; port = os.getenv('PORT', '8080'); import uvicorn; uvicorn.run('main:app', host='0.0.0.0', port=int(port))"
