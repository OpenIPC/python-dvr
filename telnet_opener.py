#!/usr/bin/env python3

from dvrip import DVRIPCam
import argparse
import datetime
import json
import os
import re
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
    board = conf.get(swver, XMV4)
    desc["SupportFlashType"] = [{"FlashID": fid} for fid in board["flashes"]]


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


def _read_until(sock, token, timeout):
    deadline = time.monotonic() + timeout
    buf = bytearray()
    sock.settimeout(0.5)
    while time.monotonic() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            if token in buf:
                break
            continue
        if not chunk:
            break
        buf.extend(chunk)
        if token in buf:
            break
    return bytes(buf)


def do_backup_via_telnet(host_ip, nfs_share, mount_point="/utils"):
    if not check_port(host_ip, 23):
        print(f"Telnet (port 23) is not open on {host_ip}.")
        print("Enable it first by running: "
              f"python3 telnet_opener.py {host_ip}")
        return

    print(f"Connecting to {host_ip}:23 as root/xmhdipc")
    s = socket.create_connection((host_ip, 23), timeout=10)
    _read_until(s, b"login:", 5)
    s.sendall(b"root\n")
    _read_until(s, b"assword:", 5)
    s.sendall(b"xmhdipc\n")
    _read_until(s, b"# ", 5)

    s.sendall(f"mkdir -p {mount_point}\n".encode())
    _read_until(s, b"# ", 5)
    s.sendall(f"mount -o nolock {nfs_share} {mount_point}\n".encode())
    out = _read_until(s, b"# ", 10).decode(errors="replace")
    print(out.strip())

    s.sendall(b"cat /sys/class/net/eth0/address\n")
    out = _read_until(s, b"# ", 5).decode(errors="replace")
    m = re.search(r"([0-9a-f]{2}(?::[0-9a-f]{2}){5})", out.lower())
    mac = m.group(1) if m else "unknown"

    backup_path = f"{mount_point}/backup-{mac}"
    print(f"Running ipctool backup -> {backup_path}")
    s.sendall(f"{mount_point}/ipctool backup {backup_path}\n".encode())
    out = _read_until(s, b"# ", 120).decode(errors="replace")
    print(out.strip())

    s.sendall(f"umount {mount_point}\n".encode())
    _read_until(s, b"# ", 5)
    s.sendall(b"exit\n")
    s.close()
    print(f"Done. Backup file is at {nfs_share.rstrip('/')}/backup-{mac} "
          "on your NFS server.")


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
    nfs_share = kwargs.get("nfs")
    user = kwargs.get("username", "admin")
    password = kwargs.get("password", "")

    if make_backup:
        if not nfs_share:
            print("--backup requires --nfs HOST:/exported/path "
                  "(NFS share with ipctool, where the backup will be written)")
            return
        do_backup_via_telnet(host_ip, nfs_share)
        return

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
    else:
        upcmd.append(cmd_armebenv(swver))
    desc["UpgradeCommand"] = upcmd
    add_flashes(desc, swver)

    zipfname = "upgrade.bin"
    make_zip(zipfname, json.dumps(desc, indent=2))
    cam.upgrade(zipfname)
    cam.close()
    os.remove(zipfname)

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
        "-b",
        "--backup",
        action="store_true",
        help="Telnet in (root/xmhdipc), mount NFS, run ipctool backup "
             "(requires --nfs and telnet already enabled on the camera)",
    )
    parser.add_argument(
        "-t",
        "--telnet",
        action="store_true",
        help="Open telnet port without rebooting camera",
    )
    parser.add_argument(
        "--nfs",
        help="NFS share for --backup, e.g. 10.0.0.1:/srv/ipctool. "
             "Must contain the ipctool binary; backup-<MAC> is written here.",
    )
    args = parser.parse_args()
    open_telnet(args.hostname, TELNET_PORT, **vars(args))


if __name__ == "__main__":
    main()
