#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os, sys, struct, json
from time import sleep
from socket import *
from datetime import *

if len(sys.argv) > 1:
    port = sys.argv[1]
else:
    print("Usage: %s [Port]" % os.path.basename(sys.argv[0]))
    port = input("Port(default 15002): ")
if port == "":
    port = "15002"
server = socket(AF_INET, SOCK_STREAM)
server.bind(("0.0.0.0", int(port)))
# server.settimeout(0.5)
server.listen(1)

log = "info.txt"


def tolog(s):
    logfile = open(datetime.now().strftime("%Y_%m_%d_") + log, "a+")
    logfile.write(s)
    logfile.close()


def GetIP(s):
    return inet_ntoa(struct.pack("<I", int(s, 16)))


while True:
    try:
        conn, addr = server.accept()
        head, version, session, sequence_number, msgid, len_data = struct.unpack(
            "BB2xII2xHI", conn.recv(20)
        )
        sleep(0.1)  # Just for recive whole packet
        data = conn.recv(len_data)
        conn.close()
        reply = json.loads(data, encoding="utf8")
        print(datetime.now().strftime("[%Y-%m-%d %H:%M:%S]>>>"))
        print(head, version, session, sequence_number, msgid, len_data)
        print(json.dumps(reply, indent=4, sort_keys=True))
        print("<<<")
        tolog(repr(data) + "\r\n")
    except (KeyboardInterrupt, SystemExit):
        break
    # except:
    # 	e = 1
    # print "no"
server.close()
sys.exit(1)
