#!/usr/bin/env python3

from dvrip import DVRIPCam
from telnetlib import Telnet
import argparse
import datetime
import json
import os
import socket
import time
import requests
import zipfile

TELNET_PORT = 4321
ARCHIVE_URL = "https://github.com/widgetii/xmupdates/raw/main/archive"

"""
    Tested on XM boards:
    IPG-53H20PL-S       53H20L_S39                  00002532
    IPG-80H20PS-S       50H20L                      00022520
    IVG-85HF20PYA-S     HI3516EV200_50H20AI_S38     000559A7
    IVG-85HG50PYA-S     HI3516EV300_85H50AI         000529B2

Issues with: "armbenv: can't load library 'libdvr.so'"
    IPG-50HV20PES-S     50H20L_18EV200_S38          00018520
"""

# downgrade archive (mainly Yandex.Disk)
# https://www.cctvsp.ru/articles/obnovlenie-proshivok-dlya-ip-kamer-ot-xiong-mai

XMV4 = {
    "envtool": "XmEnv",
    "flashes": [
        "0x00EF4017",
        "0x00EF4018",
        "0x00C22017",
        "0x00C22018",
        "0x00C22019",
        "0x00C84017",
        "0x00C84018",
        "0x001C7017",
        "0x001C7018",
        "0x00207017",
        "0x00207018",
        "0x000B4017",
        "0x000B4018",
    ],
}


def down(template, filename):
    t = template.copy()
    t['downgrade'] = filename
    return t


# Borrowed from InstallDesc
conf = {
    "000559A7": down(XMV4, "General_IPC_HI3516EV200_50H20AI_S38.Nat.dss.OnvifS.HIK_V5.00.R02.20200507_all.bin"),
    "000529B2": down(XMV4, "General_IPC_HI3516EV300_85H50AI_Nat_dss_OnvifS_HIK_V5_00_R02_20200507.bin"),
    "000529E9": down(XMV4, "hacked_from_HI3516EV300_85H50AI.bin"),
}


def add_flashes(desc, swver):
    board = conf.get(swver)
    if board is None:
        return

    fls = []
    for i in board["flashes"]:
        fls.append({"FlashID": i})
    desc["SupportFlashType"] = fls


def get_envtool(swver):
    board = conf.get(swver)
    if board is None:
        return "armbenv"

    return board["envtool"]


def make_zip(filename, data):
    zipf = zipfile.ZipFile(filename, "w", zipfile.ZIP_DEFLATED)
    zipf.writestr("InstallDesc", data)
    zipf.close()


def check_port(host_ip, port):
    a_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result_of_check = a_socket.connect_ex((host_ip, port))
    return result_of_check == 0


def extract_gen(swver):
    return swver.split(".")[3]


def cmd_armebenv(swver):
    envtool = get_envtool(swver)
    return {
        "Command": "Shell",
        "Script": f"{envtool} -s xmuart 0; {envtool} -s telnetctrl 1",
    }


def cmd_telnetd(port):
    return {
        "Command": "Shell",
        "Script": f"busybox telnetd -F -p {port} -l /bin/sh",
    }


def cmd_backup():
    return [
        {
            "Command": "Shell",
            "Script": "mount -o nolock 95.217.179.189:/srv/ro /utils/",
        },
        {"Command": "Shell", "Script": "/utils/ipctool -w"},
    ]


def downgrade_old_version(cam, buildtime, swver):
    milestone = datetime.date(2020, 5, 7)
    dto = datetime.datetime.strptime(buildtime, "%Y-%m-%d %H:%M:%S")
    if dto.date() > milestone:
        print(
            f"Current firmware date {dto.date()}, but it needs to be no more than"
            f" {milestone}\nConsider downgrade and only then continue.\n\n"
        )
        a = input("Are you sure to overwrite current firmware without backup (y/n)? ")
        if a == "y":
            board = conf.get(swver)
            if board is None:
                print(f"{swver} firmware is not supported yet")
                return False

            print("DOWNGRADING\n")
            url = f"{ARCHIVE_URL}/{swver}/{board['downgrade']}"
            print(f"Downloading {url}")
            r = requests.get(url, allow_redirects=True)
            if r.status_code != requests.codes.ok:
                print("Something went wrong")
                return False

            open('upgrade.bin', 'wb').write(r.content)
            print(f"Upgrading...")
            cam.upgrade('upgrade.bin')
            print("Completed. Wait a minute and then rerun")
            return False

        return False
    return True


def open_telnet(host_ip, port, **kwargs):
    make_telnet = kwargs.get("telnet", False)
    make_backup = kwargs.get("backup", False)
    user = kwargs.get("username", "admin")
    password = kwargs.get("password", "")

    cam = DVRIPCam(host_ip, user=user, password=password)
    if not cam.login():
        print(f"Cannot connect {host_ip}")
        return
    upinfo = cam.get_upgrade_info()
    hw = upinfo["Hardware"]
    sysinfo = cam.get_system_info()
    swver = extract_gen(sysinfo["SoftWareVersion"])
    print(f"Modifying camera {hw}, firmware {swver}")
    if not downgrade_old_version(cam, sysinfo["BuildTime"], swver):
        cam.close()
        return

    print(f"Firmware generation {swver}")

    desc = {
        "Hardware": hw,
        "DevID": f"{swver}1001000000000000",
        "CompatibleVersion": 2,
        "Vendor": "General",
        "CRC": "1ce6242100007636",
    }
    upcmd = []
    if make_telnet:
        upcmd.append(cmd_telnetd(port))
    elif make_backup:
        upcmd = cmd_backup()
    else:
        upcmd.append(cmd_armebenv(swver))
    desc["UpgradeCommand"] = upcmd
    add_flashes(desc, swver)

    zipfname = "upgrade.bin"
    make_zip(zipfname, json.dumps(desc, indent=2))
    cam.upgrade(zipfname)
    cam.close()
    os.remove(zipfname)

    if make_backup:
        print("Check backup")
        return

    if not make_telnet:
        port = 23
        print("Waiting for camera is rebooting...")

    for i in range(10):
        time.sleep(4)
        if check_port(host_ip, port):
            tport = f" {port}" if port != 23 else ""
            print(f"Now use 'telnet {host_ip}{tport}' to login")
            return

    print("Something went wrong")
    return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("hostname", help="Camera IP address or hostname")
    parser.add_argument(
        "-u", "--username", default="admin", help="Username for camera login"
    )
    parser.add_argument(
        "-p", "--password", default="", help="Password for camera login"
    )
    parser.add_argument(
        "-b", "--backup", action="store_true", help="Make backup to the cloud"
    )
    parser.add_argument(
        "-t",
        "--telnet",
        action="store_true",
        help="Open telnet port without rebooting camera",
    )
    args = parser.parse_args()
    open_telnet(args.hostname, TELNET_PORT, **vars(args))


if __name__ == "__main__":
    main()
