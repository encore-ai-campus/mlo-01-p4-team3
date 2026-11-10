FROM python:3.14.4-slim@sha256:2ca02f32b4d9d893863367ce07ec1972819f476dd38d8612f2a9cb6a41cbb727
RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 +    && rm -rf /var/lib/apt/lists/*
COPY requirements/ml.txt /tmp/requirements-ml.txt
RUN pip install --no-cache-dir --only-binary=:all: -r /tmp/requirements-ml.txt +    && rm /tmp/requirements-ml.txt
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
WORKDIR /inputs/repository
ENTRYPOINT ["python", "-m", "ops.mlflow.retrain"]
