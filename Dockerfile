FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes git make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN make test

CMD ["make", "run"]
