FROM python:slim

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
      ffmpeg

WORKDIR /app

COPY . .

CMD [ "python3", "./download-local-files.py"]
