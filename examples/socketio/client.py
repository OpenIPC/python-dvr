import socketio

# standard Python
sio = socketio.Client()

@sio.event
def connect():
  print("I'm connected!")

@sio.event
def connect_error():
  print("The connection failed!")

@sio.on('message')
def on_message(data):
  print('frame', data)

sio.connect('http://localhost:8888')