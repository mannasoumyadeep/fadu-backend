FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Add this at the bottom of main.py
COPY main.py .
RUN echo 'if __name__ == "__main__":\n    import uvicorn\n    import os\n    port = int(os.environ.get("PORT", 8080))\n    uvicorn.run("main:app", host="0.0.0.0", port=port)' >> main.py

CMD ["python", "main.py"]
