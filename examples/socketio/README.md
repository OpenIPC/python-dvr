### SocketIO example

Build image
```bash
docker build -t video-stream .
```

Run container
```bash
docker run -d \
  --restart always \
  --network host \
  --name video-stream \
  video-stream
```
