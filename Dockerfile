FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py config.toml ./
COPY jobbot ./jobbot

# Keep state outside the image so it survives rebuilds.
ENV JOBBOT_STATE=/data/seen.json
VOLUME /data

CMD ["python", "-u", "bot.py", "--loop", "10"]
