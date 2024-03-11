import socketio
from asyncio_dvrip import DVRIPCam
from aiohttp import web
import asyncio
import signal
import traceback
import base64

loop   = asyncio.get_event_loop()
queue  = asyncio.Queue()

# socket clients
clients  = []
sio = socketio.AsyncServer()
app = web.Application()
sio.attach(app)

@sio.event
def connect(sid, environ):
  print("connect ", sid)
  clients.append(sid)

@sio.event
def my_message(sid, data):
  print('message ', data)

@sio.event
def disconnect(sid):
  print('disconnect ', sid)
  clients.remove(sid)

def stop(loop):
  loop.remove_signal_handler(signal.SIGTERM)
  tasks = asyncio.gather(*asyncio.Task.all_tasks(loop=loop), loop=loop, return_exceptions=True)
  tasks.add_done_callback(lambda t: loop.stop())
  tasks.cancel()

async def stream(loop, queue):
  cam = DVRIPCam("192.168.0.100", port=34567, user="admin", password="")
  # login
  if not await cam.login(loop):
    raise Exception("Can't open cam")

  try:
    await cam.start_monitor(lambda frame, meta, user: queue.put_nowait(frame), stream="Main")
  except Exception as err:
    msg = ''.join(traceback.format_tb(err.__traceback__) + [str(err)])
    print(msg)
  finally:
    cam.stop_monitor()
    cam.close()

async def process(queue, lock):
  while True:
    frame = await queue.get()

    if frame:
      await lock.acquire()
      try:
        for sid in clients:
          await sio.emit('message', {'data': base64.b64encode(frame).decode("utf-8")}, room=sid)
      finally:
        lock.release()

async def worker(loop, queue, lock):
  task = None

  # infinyty loop
  while True:
    await lock.acquire()

    try:
      # got clients and task not started
      if len(clients) > 0 and task is None:
        # create stream task
        task = loop.create_task(stream(loop, queue))

      # no more clients, neet stop task
      if len(clients) == 0 and task is not None:
        # I don't like this way, maybe someone can do it better
        task.cancel()
        task = None
      await asyncio.sleep(0.1)
    except Exception as err:
      msg = ''.join(traceback.format_tb(err.__traceback__) + [str(err)])
      print(msg)
    finally:
      lock.release()

if __name__ == '__main__':
  try:
    lock = asyncio.Lock()

    # run wb application
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, host='0.0.0.0', port=8888)
    loop.run_until_complete(site.start())

    # run worker
    loop.create_task(worker(loop, queue, lock))
    loop.create_task(process(queue, lock))

    # wait stop
    loop.run_forever()
  except:
    stop(loop)
