# A container build of the same app, for any platform that takes an image (Fly.io, Koyeb,
# Cloud Run, Render, DigitalOcean App Platform).
#
# It runs the same command DEPLOY.md documents and render.yaml uses, so the container and a
# buildpack deploy are the same server started the same way -- and it is the command that was
# run against this checkout to verify the setup, not a different one that only exists here.
FROM python:3.11-slim

# Unbuffered output so a platform's logs arrive as they are written, and no .pyc files because
# a container's filesystem does not outlive the process.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080

WORKDIR /app

# Dependencies first, so editing the app does not rebuild them.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# api-key.txt and *-token.txt are excluded by .dockerignore: secrets are set as environment
# variables on the platform, never baked into an image.
COPY . .

EXPOSE 8080

CMD ["sh", "-c", "waitress-serve --host=0.0.0.0 --port=${PORT} --threads=8 --channel-timeout=300 wsgi:app"]
