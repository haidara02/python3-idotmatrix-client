FROM python:3.12-slim

WORKDIR /app
COPY service/requirements.txt service/requirements.txt
RUN pip install --no-cache-dir -r service/requirements.txt
COPY service/ service/

ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "service"]
CMD ["run"]
