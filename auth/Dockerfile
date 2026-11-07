FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY schema.sql app.py ./

ENV BIND=127.0.0.1
ENV PORT=8090
EXPOSE 8090

CMD ["python", "app.py"]
