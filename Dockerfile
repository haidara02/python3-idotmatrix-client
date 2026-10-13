FROM python:3.12-slim

# bluetoothctl, to clear connections left behind by a previous run
RUN apt-get update && apt-get install -y --no-install-recommends bluez && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY service/requirements.txt service/requirements.txt
RUN pip install --no-cache-dir -r service/requirements.txt
COPY service/ service/

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "service"]
CMD ["run"]
