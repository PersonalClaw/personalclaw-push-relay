FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY relay ./relay
RUN pip install --no-cache-dir .
EXPOSE 8080
# Runs as a non-root user: the relay needs no filesystem writes at all.
RUN useradd -r relay
USER relay
CMD ["push-relay"]
