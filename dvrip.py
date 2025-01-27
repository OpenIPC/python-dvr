import os
import struct
import json
from time import sleep
import hashlib
import threading
from socket import socket, AF_INET, SOCK_STREAM, SOCK_DGRAM, SOL_SOCKET
from datetime import *
from re import compile
import time
import logging
from pathlib import Path


class SomethingIsWrongWithCamera(Exception):
    pass


class DVRIPCam(object):
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
    CODES = {
        100: "OK",
        101: "Unknown error",
        102: "Unsupported version",
        103: "Request not permitted",
        104: "User already logged in",
        105: "User is not logged in",
        106: "Username or password is incorrect",
        107: "User does not have necessary permissions",
        203: "Password is incorrect",
        511: "Start of upgrade",
        512: "Upgrade was not started",
        513: "Upgrade data errors",
        514: "Upgrade error",
        515: "Upgrade successful",
    }
    QCODES = {
        "AuthorityList": 1470,
        "Users": 1472,
        "Groups": 1474,
        "AddGroup": 1476,
        "ModifyGroup": 1478,
        "DelGroup": 1480,
        "User": 1482,
        "ModifyUser": 1484,
        "DelUser": 1486,
        "ModifyPassword": 1488,
        "AlarmInfo": 1504,
        "AlarmSet": 1500,
        "ChannelTitle": 1046,
        "EncodeCapability": 1360,
        "General": 1042,
        "KeepAlive": 1006,
        "OPMachine": 1450,
        "OPMailTest": 1636,
        "OPMonitor": 1413,
        "OPNetKeyboard": 1550,
        "OPPTZControl": 1400,
        "OPSNAP": 1560,
        "OPSendFile": 0x5F2,
        "OPSystemUpgrade": 0x5F5,
        "OPTalk": 1434,
        "OPTimeQuery": 1452,
        "OPTimeSetting": 1450,
        "NetWork.NetCommon": 1042,
        "OPNetAlarm": 1506,
        "SystemFunction": 1360,
        "SystemInfo": 1020,
    }
    OPFEED_QCODES = {
        "OPFeedBook": {
            "SET": 2300,
            "GET": 2302,
        },
        "OPFeedManual": {
            "SET": 2304,
        },
        "OPFeedHistory": {
            "GET": 2306,
            "SET": 2308,
        },
    }
    KEY_CODES = {
        "M": "Menu",
        "I": "Info",
        "E": "Esc",
        "F": "Func",
        "S": "Shift",
        "L": "Left",
        "U": "Up",
        "R": "Right",
        "D": "Down",
    }
    OK_CODES = [100, 515]
    PORTS = {
        "tcp": 34567,
        "udp": 34568,
    }

    def __init__(self, ip, **kwargs):
        self.logger = logging.getLogger(__name__)
        self.ip = ip
        self.iface = kwargs.get("iface", None)
        self.user = kwargs.get("user", "admin")
        hash_pass = kwargs.get("hash_pass")
        self.hash_pass = kwargs.get(
            "hash_pass", self.sofia_hash(kwargs.get("password", ""))
        )
        self.proto = kwargs.get("proto", "tcp")
        self.port = kwargs.get("port", self.PORTS.get(self.proto))
        self.socket = None
        self.packet_count = 0
        self.session = 0
        self.alive_time = 20
        self.alive = None
        self.alarm = None
        self.alarm_func = None
        self.busy = threading.Condition()

    def debug(self, format=None):
        self.logger.setLevel(logging.DEBUG)
        ch = logging.StreamHandler()
        if format:
            formatter = logging.Formatter(format)
            ch.setFormatter(formatter)
        self.logger.addHandler(ch)

    def connect(self, timeout=10):
        try:
            if self.proto == "tcp":
                self.socket_send = self.tcp_socket_send
                self.socket_recv = self.tcp_socket_recv
                self.socket = socket(AF_INET, SOCK_STREAM)
                if self.iface:
                    self.socket.setsockopt(
                        SOL_SOCKET, 25, str(self.iface + '\0').encode())
                self.socket.connect((self.ip, self.port))
            elif self.proto == "udp":
                self.socket_send = self.udp_socket_send
                self.socket_recv = self.udp_socket_recv
                self.socket = socket(AF_INET, SOCK_DGRAM)
            else:
                raise f"Unsupported protocol {self.proto}"

            # it's important to extend timeout for upgrade procedure
            self.timeout = timeout
            self.socket.settimeout(timeout)
        except OSError:
            raise SomethingIsWrongWithCamera("Cannot connect to camera")

    def close(self):
        try:
            self.alive.cancel()
            self.socket.close()
        except:
            pass
        self.socket = None

    def udp_socket_send(self, bytes):
        return self.socket.sendto(bytes, (self.ip, self.port))

    def udp_socket_recv(self, bytes):
        data, _ = self.socket.recvfrom(bytes)
        return data

    def tcp_socket_send(self, bytes):
        try:
            return self.socket.sendall(bytes)
        except:
            return None

    def tcp_socket_recv(self, bufsize):
        try:
            return self.socket.recv(bufsize)
        except:
            return None

    def receive_with_timeout(self, length):
        received = 0
        buf = bytearray()
        start_time = time.time()

        while True:
            data = self.socket_recv(length - received)
            buf.extend(data)
            received += len(data)
            if length == received:
                break
            elapsed_time = time.time() - start_time
            if elapsed_time > self.timeout:
                return None
        return buf

    def receive_json(self, length):
        data = self.receive_with_timeout(length)
        if data is None:
            return {}

        self.packet_count += 1
        self.logger.debug("<= %s", data)
        try:
            reply = json.loads(data[:-2])
            return reply
        except:
            return data

    def send_custom(
        self, msg, data={}, wait_response=True, download=False, version=0
    ):
        if self.socket is None:
            return {"Ret": 101}
        # self.busy.wait()
        self.busy.acquire()
        if hasattr(data, "__iter__"):
            if version == 1:
                data["SessionID"] = f"{self.session:#0{12}x}"
            data = bytes(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")), "utf-8"
            )

        tail = b"\x00"
        if version == 0:
            tail = b"\x0a" + tail
        pkt = (
            struct.pack(
                "BB2xII2xHI",
                255,
                version,
                self.session,
                self.packet_count,
                msg,
                len(data) + len(tail),
            )
            + data
            + tail
        )
        self.logger.debug("=> %s", pkt)
        self.socket_send(pkt)
        if wait_response:
            reply = {"Ret": 101}
            data = self.socket_recv(20)
            if data is None or len(data) < 20:
                return None
            (
                head,
                version,
                self.session,
                sequence_number,
                msgid,
                len_data,
            ) = struct.unpack("BB2xII2xHI", data)

            reply = None
            if download:
                reply = self.get_file(len_data)
            else:
                reply = self.get_specific_size(len_data)
            self.busy.release()
            return reply

    def send(self, msg, data={}, wait_response=True):
        if self.socket is None:
            return {"Ret": 101}
        # self.busy.wait()
        self.busy.acquire()
        if hasattr(data, "__iter__"):
            data = bytes(json.dumps(data, ensure_ascii=False), "utf-8")
        pkt = (
            struct.pack(
                "BB2xII2xHI",
                255,
                0,
                self.session,
                self.packet_count,
                msg,
                len(data) + 2,
            )
            + data
            + b"\x0a\x00"
        )
        self.logger.debug("=> %s", pkt)
        self.socket_send(pkt)
        if wait_response:
            reply = {"Ret": 101}
            data = self.socket_recv(20)
            if data is None or len(data) < 20:
                return None
            (
                head,
                version,
                self.session,
                sequence_number,
                msgid,
                len_data,
            ) = struct.unpack("BB2xII2xHI", data)
            reply = self.receive_json(len_data)
            self.busy.release()
            return reply

    def sofia_hash(self, password=""):
        md5 = hashlib.md5(bytes(password, "utf-8")).digest()
        chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        return "".join([chars[sum(x) % 62] for x in zip(md5[::2], md5[1::2])])

    def login(self):
        if self.socket is None:
            self.connect()
        data = self.send(
            1000,
            {
                "EncryptType": "MD5",
                "LoginType": "DVRIP-Web",
                "PassWord": self.hash_pass,
                "UserName": self.user,
            },
        )
        if data is None or data["Ret"] not in self.OK_CODES:
            return False
        self.session = int(data["SessionID"], 16)
        self.alive_time = data["AliveInterval"]
        self.keep_alive()
        if not hasattr(self, 'devtype'):
            self.devtype = data["DeviceType "]
        return data["Ret"] in self.OK_CODES

    def getAuthorityList(self):
        data = self.send(self.QCODES["AuthorityList"])
        if data["Ret"] in self.OK_CODES:
            return data["AuthorityList"]
        else:
            return []

    def getGroups(self):
        data = self.send(self.QCODES["Groups"])
        if data["Ret"] in self.OK_CODES:
            return data["Groups"]
        else:
            return []

    def addGroup(self, name, comment="", auth=None):
        data = self.set_command(
            "AddGroup",
            {
                "Group": {
                    "AuthorityList": auth or self.getAuthorityList(),
                    "Memo": comment,
                    "Name": name,
                },
            },
        )
        return data["Ret"] in self.OK_CODES

    def modifyGroup(self, name, newname=None, comment=None, auth=None):
        g = [x for x in self.getGroups() if x["Name"] == name]
        if g == []:
            print(f'Group "{name}" not found!')
            return False
        g = g[0]
        data = self.send(
            self.QCODES["ModifyGroup"],
            {
                "Group": {
                    "AuthorityList": auth or g["AuthorityList"],
                    "Memo": comment or g["Memo"],
                    "Name": newname or g["Name"],
                },
                "GroupName": name,
            },
        )
        return data["Ret"] in self.OK_CODES

    def delGroup(self, name):
        data = self.send(
            self.QCODES["DelGroup"],
            {
                "Name": name,
                "SessionID": "0x%08X" % self.session,
            },
        )
        return data["Ret"] in self.OK_CODES

    def getUsers(self):
        data = self.send(self.QCODES["Users"])
        if data["Ret"] in self.OK_CODES:
            return data["Users"]
        else:
            return []

    def addUser(
        self, name, password, comment="", group="user", auth=None, sharable=True
    ):
        g = [x for x in self.getGroups() if x["Name"] == group]
        if g == []:
            print(f'Group "{group}" not found!')
            return False
        g = g[0]
        data = self.set_command(
            "User",
            {
                "AuthorityList": auth or g["AuthorityList"],
                "Group": g["Name"],
                "Memo": comment,
                "Name": name,
                "Password": self.sofia_hash(password),
                "Reserved": False,
                "Sharable": sharable,
            },
        )
        return data["Ret"] in self.OK_CODES

    def modifyUser(
        self, name, newname=None, comment=None, group=None, auth=None, sharable=None
    ):
        u = [x for x in self.getUsers() if x["Name"] == name]
        if u == []:
            print(f'User "{name}" not found!')
            return False
        u = u[0]
        if group:
            g = [x for x in self.getGroups() if x["Name"] == group]
            if g == []:
                print(f'Group "{group}" not found!')
                return False
            u["AuthorityList"] = g[0]["AuthorityList"]
        data = self.send(
            self.QCODES["ModifyUser"],
            {
                "User": {
                    "AuthorityList": auth or u["AuthorityList"],
                    "Group": group or u["Group"],
                    "Memo": comment or u["Memo"],
                    "Name": newname or u["Name"],
                    "Password": "",
                    "Reserved": u["Reserved"],
                    "Sharable": sharable or u["Sharable"],
                },
                "UserName": name,
            },
        )
        return data["Ret"] in self.OK_CODES

    def delUser(self, name):
        data = self.send(
            self.QCODES["DelUser"],
            {
                "Name": name,
                "SessionID": "0x%08X" % self.session,
            },
        )
        return data["Ret"] in self.OK_CODES

    def changePasswd(self, newpass="", oldpass=None, user=None):
        data = self.send(
            self.QCODES["ModifyPassword"],
            {
                "EncryptType": "MD5",
                "NewPassWord": self.sofia_hash(newpass),
                "PassWord": oldpass or self.hash_pass,
                "SessionID": "0x%08X" % self.session,
                "UserName": user or self.user,
            },
        )
        return data["Ret"] in self.OK_CODES

    def channel_title(self, titles):
        if isinstance(titles, str):
            titles = [titles]
        self.send(
            self.QCODES["ChannelTitle"],
            {
                "ChannelTitle": titles,
                "Name": "ChannelTitle",
                "SessionID": "0x%08X" % self.session,
            },
        )

    def channel_bitmap(self, width, height, bitmap):
        header = struct.pack("HH12x", width, height)
        self.socket_send(
            struct.pack(
                "BB2xII2xHI",
                255,
                0,
                self.session,
                self.packet_count,
                0x041A,
                len(bitmap) + 16,
            )
            + header
            + bitmap
        )
        reply, rcvd = self.recv_json()
        if reply and reply["Ret"] != 100:
            return False
        return True

    def reboot(self):
        self.set_command("OPMachine", {"Action": "Reboot"})
        self.close()

    def setAlarm(self, func):
        self.alarm_func = func

    def clearAlarm(self):
        self.alarm_func = None

    def alarmStart(self):
        self.alarm = threading.Thread(
            name="DVRAlarm%08X" % self.session,
            target=self.alarm_thread,
            args=[self.busy],
        )
        res = self.get_command("", self.QCODES["AlarmSet"])
        self.alarm.start()
        return res

    def alarm_thread(self, event):
        while True:
            event.acquire()
            try:
                (
                    head,
                    version,
                    session,
                    sequence_number,
                    msgid,
                    len_data,
                ) = struct.unpack("BB2xII2xHI", self.socket_recv(20))
                sleep(0.1)  # Just for receive whole packet
                reply = self.socket_recv(len_data)
                self.packet_count += 1
                reply = json.loads(reply[:-2])
                if msgid == self.QCODES["AlarmInfo"] and self.session == session:
                    if self.alarm_func is not None:
                        self.alarm_func(reply[reply["Name"]], sequence_number)
            except:
                pass
            finally:
                event.release()
            if self.socket is None:
                break

    def set_remote_alarm(self, state):
        self.set_command(
            "OPNetAlarm",
            {"Event": 0, "State": state},
        )

    def keep_alive(self):
        ret = self.send(
            self.QCODES["KeepAlive"],
            {"Name": "KeepAlive", "SessionID": "0x%08X" % self.session},
        )
        if ret is None:
            self.close()
            return
        self.alive = threading.Timer(self.alive_time, self.keep_alive)
        self.alive.daemon = True
        self.alive.start()

    def keyDown(self, key):
        self.set_command(
            "OPNetKeyboard",
            {"Status": "KeyDown", "Value": key},
        )

    def keyUp(self, key):
        self.set_command(
            "OPNetKeyboard",
            {"Status": "KeyUp", "Value": key},
        )

    def keyPress(self, key):
        self.keyDown(key)
        sleep(0.3)
        self.keyUp(key)

    def keyScript(self, keys):
        for k in keys:
            if k != " " and k.upper() in self.KEY_CODES:
                self.keyPress(self.KEY_CODES[k.upper()])
            else:
                sleep(1)

    def ptz(self, cmd, step=5, preset=-1, ch=0):
        CMDS = [
            "DirectionUp",
            "DirectionDown",
            "DirectionLeft",
            "DirectionRight",
            "DirectionLeftUp",
            "DirectionLeftDown",
            "DirectionRightUp",
            "DirectionRightDown",
            "ZoomTile",
            "ZoomWide",
            "FocusNear",
            "FocusFar",
            "IrisSmall",
            "IrisLarge",
            "SetPreset",
            "GotoPreset",
            "ClearPreset",
            "StartTour",
            "StopTour",
        ]
        # ptz_param = { "AUX" : { "Number" : 0, "Status" : "On" }, "Channel" : ch, "MenuOpts" : "Enter", "POINT" : { "bottom" : 0, "left" : 0, "right" : 0, "top" : 0 }, "Pattern" : "SetBegin", "Preset" : -1, "Step" : 5, "Tour" : 0 }
        ptz_param = {
            "AUX": {"Number": 0, "Status": "On"},
            "Channel": ch,
            "MenuOpts": "Enter",
            "Pattern": "Start",
            "Preset": preset,
            "Step": step,
            "Tour": 1 if "Tour" in cmd else 0,
        }
        return self.set_command(
            "OPPTZControl",
            {"Command": cmd, "Parameter": ptz_param},
        )

    def set_info(self, command, data):
        return self.set_command(command, data, 1040)

    def set_command(self, command, data, code=None):
        if not code:
            code = self.OPFEED_QCODES.get(command)
            if code:
                code = code.get("SET")
        if not code:
            code = self.QCODES[command]
        return self.send(
            code, {"Name": command, "SessionID": "0x%08X" % self.session, command: data}
        )

    def get_info(self, command):
        return self.get_command(command, 1042)

    def get_command(self, command, code=None):
        if not code:
            code = self.OPFEED_QCODES.get(command)
            if code:
                code = code.get("GET")
        if not code:
            code = self.QCODES[command]

        data = self.send(code, {"Name": command, "SessionID": "0x%08X" % self.session})
        if data["Ret"] in self.OK_CODES and command in data:
            return data[command]
        else:
            return data

    def get_time(self):
        return datetime.strptime(self.get_command("OPTimeQuery"), self.DATE_FORMAT)

    def set_time(self, time=None):
        if time is None:
            time = datetime.now()
        return self.set_command("OPTimeSetting", time.strftime(self.DATE_FORMAT))

    def get_netcommon(self):
        return self.get_command("NetWork.NetCommon")

    def get_system_info(self):
        return self.get_command("SystemInfo")

    def get_general_info(self):
        return self.get_command("General")

    def get_encode_capabilities(self):
        return self.get_command("EncodeCapability")

    def get_system_capabilities(self):
        return self.get_command("SystemFunction")

    def get_camera_info(self, default_config=False):
        """Request data for 'Camera' from  the target DVRIP device."""
        if default_config:
            code = 1044
        else:
            code = 1042
        return self.get_command("Camera", code)

    def get_encode_info(self, default_config=False):
        """Request data for 'Simplify.Encode' from the target DVRIP device.

        Arguments:
        default_config -- returns the default values for the type if True
        """
        if default_config:
            code = 1044
        else:
            code = 1042
        return self.get_command("Simplify.Encode", code)

    def recv_json(self, buf=bytearray()):
        p = compile(b".*({.*})")

        packet = self.socket_recv(0xFFFF)
        if not packet:
            return None, buf
        buf.extend(packet)
        m = p.search(buf)
        if m is None:
            return None, buf
        buf = buf[m.span(1)[1] :]
        return json.loads(m.group(1)), buf

    def get_upgrade_info(self):
        return self.get_command("OPSystemUpgrade")

    def upgrade(self, filename="", packetsize=0x8000, vprint=None):
        if not vprint:
            vprint = lambda *args, **kwargs: print(*args, **kwargs)

        data = self.set_command(
            "OPSystemUpgrade", {"Action": "Start", "Type": "System"}, 0x5F0
        )
        if data["Ret"] not in self.OK_CODES:
            return data

        self.logger.debug(f"Sending file: {filename}")
        blocknum = 0
        sentbytes = 0
        fsize = os.stat(filename).st_size
        rcvd = bytearray()
        with open(filename, "rb") as f:
            while True:
                bytes = f.read(packetsize)
                if not bytes:
                    break
                header = struct.pack(
                    "BB2xII2xHI", 255, 0, self.session, blocknum, 0x5F2, len(bytes)
                )
                self.socket_send(header + bytes)
                blocknum += 1
                sentbytes += len(bytes)

                reply, rcvd = self.recv_json(rcvd)
                if reply and reply["Ret"] != 100:
                    vprint("\nUpgrade failed")
                    return reply

                progress = sentbytes / fsize * 100
                vprint(f"Uploading: {progress:.1f}%", end='\r')
        vprint()
        self.logger.debug("Upload complete")

        pkt = struct.pack("BB2xIIxBHI", 255, 0, self.session, blocknum, 1, 0x05F2, 0)
        self.socket_send(pkt)
        self.logger.debug("Starting upgrade...")
        while True:
            data, rcvd = self.recv_json(rcvd)
            self.logger.debug(reply)
            if data is None:
                vprint("\nDone")
                return
            if data["Ret"] in [512, 514, 513]:
                vprint("\nUpgrade failed")
                return data
            if data["Ret"] == 515:
                vprint("\nUpgrade successful")
                self.socket.close()
                return data
            vprint(f"Upgrading: {data['Ret']:>3}%", end='\r')
        vprint()

    def get_file(self, first_chunk_size):
        buf = bytearray()

        data = self.receive_with_timeout(first_chunk_size)
        buf.extend(data)

        while True:
            header = self.receive_with_timeout(20)
            len_data = struct.unpack("I", header[16:])[0]

            if len_data == 0:
                return buf

            data = self.receive_with_timeout(len_data)
            buf.extend(data)

    def get_specific_size(self, size):
        return self.receive_with_timeout(size)

    def reassemble_bin_payload(self, metadata={}):
        def internal_to_type(data_type, value):
            if data_type == 0x1FC or data_type == 0x1FD:
                if value == 1:
                    return "mpeg4"
                elif value == 2:
                    return "h264"
                elif value == 3:
                    return "h265"
            elif data_type == 0x1F9:
                if value == 1 or value == 6:
                    return "info"
            elif data_type == 0x1FA:
                if value == 0xE:
                    return "g711a"
            elif data_type == 0x1FE and value == 0:
                return "jpeg"
            return None

        def internal_to_datetime(value):
            second = value & 0x3F
            minute = (value & 0xFC0) >> 6
            hour = (value & 0x1F000) >> 12
            day = (value & 0x3E0000) >> 17
            month = (value & 0x3C00000) >> 22
            year = ((value & 0xFC000000) >> 26) + 2000
            return datetime(year, month, day, hour, minute, second)

        length = 0
        buf = bytearray()
        start_time = time.time()

        while True:
            data = self.receive_with_timeout(20)
            (
                head,
                version,
                session,
                sequence_number,
                total,
                cur,
                msgid,
                len_data,
            ) = struct.unpack("BB2xIIBBHI", data)
            packet = self.receive_with_timeout(len_data)
            frame_len = 0
            if length == 0:
                media = None
                frame_len = 8
                (data_type,) = struct.unpack(">I", packet[:4])
                if data_type == 0x1FC or data_type == 0x1FE:
                    frame_len = 16
                    (
                        media,
                        metadata["fps"],
                        w,
                        h,
                        dt,
                        length,
                    ) = struct.unpack("BBBBII", packet[4:frame_len])
                    metadata["width"] = w * 8
                    metadata["height"] = h * 8
                    metadata["datetime"] = internal_to_datetime(dt)
                    if data_type == 0x1FC:
                        metadata["frame"] = "I"
                elif data_type == 0x1FD:
                    (length,) = struct.unpack("I", packet[4:frame_len])
                    metadata["frame"] = "P"
                elif data_type == 0x1FA:
                    (media, samp_rate, length) = struct.unpack(
                        "BBH", packet[4:frame_len]
                    )
                elif data_type == 0x1F9:
                    (media, n, length) = struct.unpack("BBH", packet[4:frame_len])
                # special case of JPEG shapshots
                elif data_type == 0xFFD8FFE0:
                    return packet
                else:
                    raise ValueError(data_type)
                if media is not None:
                    metadata["type"] = internal_to_type(data_type, media)
            buf.extend(packet[frame_len:])
            length -= len(packet) - frame_len
            if length == 0:
                return buf
            elapsed_time = time.time() - start_time
            if elapsed_time > self.timeout:
                return None

    def snapshot(self, channel=0):
        command = "OPSNAP"
        self.send(
            self.QCODES[command],
            {
                "Name": command,
                "SessionID": "0x%08X" % self.session,
                command: {"Channel": channel},
            },
            wait_response=False,
        )
        packet = self.reassemble_bin_payload()
        return packet

    def start_monitor(self, frame_callback, user={}, stream="Main"):
        params = {
            "Channel": 0,
            "CombinMode": "NONE",
            "StreamType": stream,
            "TransMode": "TCP",
        }
        data = self.set_command("OPMonitor", {"Action": "Claim", "Parameter": params})
        if data["Ret"] not in self.OK_CODES:
            return data

        self.send(
            1410,
            {
                "Name": "OPMonitor",
                "SessionID": "0x%08X" % self.session,
                "OPMonitor": {"Action": "Start", "Parameter": params},
            },
            wait_response=False,
        )
        self.monitoring = True
        while self.monitoring:
            meta = {}
            frame = self.reassemble_bin_payload(meta)
            frame_callback(frame, meta, user)

    def stop_monitor(self):
        self.monitoring = False

    def list_local_files(self, startTime, endTime, filetype, channel = 0):
        # 1440 OPFileQuery
        result = []
        data = self.send(
            1440,
            {
                "Name": "OPFileQuery",
                "OPFileQuery": {
                    "BeginTime": startTime,
                    "Channel": channel,
                    "DriverTypeMask": "0x0000FFFF",
                    "EndTime": endTime,
                    "Event": "*",
                    "StreamType": "0x00000000",
                    "Type": filetype,
                },
            },
        )

        if data == None:
            self.logger.debug("Could not get files.")
            raise ConnectionRefusedError("Could not get files")

        # When no file can be found
        if data["Ret"] != 100:
            self.logger.debug(
                f"No files found for channel {channel} for this time range. Start: {startTime}, End: {endTime}"
            )
            return []

        # OPFileQuery only returns the first 64 items
        # we therefore need to add the results to a list, modify the starttime with the begintime value of the last item we received and query again
        # max number of results are 511
        result = data["OPFileQuery"]

        max_event = {"status": "init", "last_num_results": 0}
        while max_event["status"] == "init" or max_event["status"] == "limit":
            if max_event["status"] == "init":
                max_event["status"] = "run"
            while len(data["OPFileQuery"]) == 64 or max_event["status"] == "limit":
                newStartTime = data["OPFileQuery"][-1]["BeginTime"]
                data = self.send(
                    1440,
                    {
                        "Name": "OPFileQuery",
                        "OPFileQuery": {
                            "BeginTime": newStartTime,
                            "Channel": channel,
                            "DriverTypeMask": "0x0000FFFF",
                            "EndTime": endTime,
                            "Event": "*",
                            "StreamType": "0x00000000",
                            "Type": filetype,
                        },
                    },
                )
                result += data["OPFileQuery"]
                max_event["status"] = "run"

            if len(result) % 511 == 0 or max_event["status"] == "limit":
                self.logger.debug("Max number of events reached...")
                if len(result) == max_event["last_num_results"]:
                    self.logger.debug(
                        "No new events since last run. All events queried"
                    )
                    return result

                max_event["status"] = "limit"
                max_event["last_num_results"] = len(result)

        self.logger.debug(f"Found {len(result)} files.")
        return result

    def ptz_step(self, cmd, step=5):
        # To do a single step the first message will just send a tilt command which last forever
        # the second command will stop the tilt movement
        # that means if second message does not arrive for some reason the camera will be keep moving in that direction forever

        parms_start = {
            "AUX": {"Number": 0, "Status": "On"},
            "Channel": 0,
            "MenuOpts": "Enter",
            "POINT": {"bottom": 0, "left": 0, "right": 0, "top": 0},
            "Pattern": "SetBegin",
            "Preset": 65535,
            "Step": step,
            "Tour": 0,
        }

        self.set_command("OPPTZControl", {"Command": cmd, "Parameter": parms_start})

        parms_end = {
            "AUX": {"Number": 0, "Status": "On"},
            "Channel": 0,
            "MenuOpts": "Enter",
            "POINT": {"bottom": 0, "left": 0, "right": 0, "top": 0},
            "Pattern": "SetBegin",
            "Preset": -1,
            "Step": step,
            "Tour": 0,
        }

        self.set_command("OPPTZControl", {"Command": cmd, "Parameter": parms_end})

    def download_file(
        self, startTime, endTime, filename, targetFilePath, download=True
    ):
        Path(targetFilePath).parent.mkdir(parents=True, exist_ok=True)

        self.logger.debug(f"Downloading: {targetFilePath}")

        self.send(
            1424,
            {
                "Name": "OPPlayBack",
                "OPPlayBack": {
                    "Action": "Claim",
                    "Parameter": {
                        "PlayMode": "ByName",
                        "FileName": filename,
                        "StreamType": 0,
                        "Value": 0,
                        "TransMode": "TCP",
                        # Maybe IntelligentPlayBack is needed in some edge case
                        # "IntelligentPlayBackEvent": "",
                        # "IntelligentPlayBackSpeed": 2031619,
                    },
                    "StartTime": startTime,
                    "EndTime": endTime,
                },
            },
        )

        actionStart = "Start"
        if download:
            actionStart = f"Download{actionStart}"

        data = self.send_custom(
            1420,
            {
                "Name": "OPPlayBack",
                "OPPlayBack": {
                    "Action": actionStart,
                    "Parameter": {
                        "PlayMode": "ByName",
                        "FileName": filename,
                        "StreamType": 0,
                        "Value": 0,
                        "TransMode": "TCP",
                        # Maybe IntelligentPlayBack is needed in some edge case
                        # "IntelligentPlayBackEvent": "",
                        # "IntelligentPlayBackSpeed": 0,
                    },
                    "StartTime": startTime,
                    "EndTime": endTime,
                },
            },
            download=True,
        )

        try:
            with open(targetFilePath, "wb") as bin_data:
                bin_data.write(data)
        except TypeError:
            Path(targetFilePath).unlink(missing_ok=True)
            self.logger.debug(f"An error occured while downloading {targetFilePath}")
            raise

        self.logger.debug(f"File successfully downloaded: {targetFilePath}")

        actionStop = "Stop"
        if download:
            actionStop = f"Download{actionStop}"

        self.send(
            1420,
            {
                "Name": "OPPlayBack",
                "OPPlayBack": {
                    "Action": actionStop,
                    "Parameter": {
                        "FileName": filename,
                        "PlayMode": "ByName",
                        "StreamType": 0,
                        "TransMode": "TCP",
                        "Channel": 0,
                        "Value": 0,
                        # Maybe IntelligentPlayBack is needed in some edge case
                        # "IntelligentPlayBackEvent": "",
                        # "IntelligentPlayBackSpeed": 0,
                    },
                    "StartTime": startTime,
                    "EndTime": endTime,
                },
            },
        )
        return None

    def get_channel_titles(self):
        return self.get_command("ChannelTitle", 1048)

    def get_channel_statuses(self):
        return self.get_info("NetWork.ChnStatus")
