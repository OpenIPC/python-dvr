#!/usr/bin/env python
# -*- coding: UTF-8 -*-
import sys
from dvrip import DVRIPCam
from time import sleep
import json

host_ip = "192.168.0.100"
if len(sys.argv) > 1:
    host_ip = str(sys.argv[1])

cam = DVRIPCam(host_ip, user="admin", password="46216")

if cam.login():
    print("Success! Connected to " + host_ip)
else:
    print("Failure. Could not connect.")

info = cam.get_info("fVideo.OSDInfo")
print(json.dumps(info, ensure_ascii=False))
info["OSDInfo"][0]["Info"] = [u"Тест00", "Test01", "Test02"]
# info["OSDInfo"][0]["Info"][1] = ""
# info["OSDInfo"][0]["Info"][2] = ""
# info["OSDInfo"][0]["Info"][3] = "Test3"
info["OSDInfo"][0]["OSDInfoWidget"]["EncodeBlend"] = True
info["OSDInfo"][0]["OSDInfoWidget"]["PreviewBlend"] = True
# info["OSDInfo"][0]["OSDInfoWidget"]["RelativePos"] = [6144,6144,8192,8192]
cam.set_info("fVideo.OSDInfo", info)
# enc_info = cam.get_info("Simplify.Encode")
# Alarm example
def alarm(content, ids):
    print(content)


cam.setAlarm(alarm)
cam.alarmStart()
# cam.get_encode_info()
# sleep(1)
# cam.get_camera_info()
# sleep(1)

# enc_info[0]['ExtraFormat']['Video']['FPS'] = 20
# cam.set_info("Simplify.Encode", enc_info)
# sleep(2)
# print(cam.get_info("Simplify.Encode"))
# cam.close()
