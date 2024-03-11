from time import sleep
from dvrip import DVRIPCam, SomethingIsWrongWithCamera
from pathlib import Path
import subprocess
import json
from datetime import datetime


class SolarCam:
    cam = None
    logger = None

    def __init__(self, host_ip, user, password, logger):
        self.logger = logger
        self.cam = DVRIPCam(
            host_ip,
            user=user,
            password=password,
        )

    def login(self, num_retries=10):
        for i in range(num_retries):
            try:
                self.logger.debug("Try login...")
                self.cam.login()
                self.logger.debug(
                    f"Success! Connected to Camera. Waiting few seconds to let Camera fully boot..."
                )
                # waiting until camera is ready
                sleep(10)
                return
            except SomethingIsWrongWithCamera:
                self.logger.debug("Could not connect...Camera could be offline")
                self.cam.close()

            if i == 9:
                raise ConnectionRefusedError(
                    f"Could not connect {num_retries} times...aborting"
                )
            sleep(2)

    def logout(self):
        self.cam.close()

    def get_time(self):
        return self.cam.get_time()

    def set_time(self, time=None):
        if time is None:
            time = datetime.now()
        return self.cam.set_time(time=time)

    def get_local_files(self, start, end, filetype):
        return self.cam.list_local_files(start, end, filetype)

    def dump_local_files(
        self, files, blacklist_path, download_dir, target_filetype=None
    ):
        with open(f"{blacklist_path}.dmp", "a") as outfile:
            for file in files:
                target_file_path = self.generateTargetFilePath(
                    file["FileName"], download_dir
                )
                outfile.write(f"{target_file_path}\n")

                if target_filetype:
                    target_file_path_convert = self.generateTargetFilePath(
                        file["FileName"], download_dir, extention=f"{target_filetype}"
                    )
                    outfile.write(f"{target_file_path_convert}\n")

    def generateTargetFilePath(self, filename, downloadDir, extention=""):
        fileExtention = Path(filename).suffix
        filenameSplit = filename.split("/")
        filenameDisk = f"{filenameSplit[3]}_{filenameSplit[5][:8]}".replace(".", "-")
        targetPathClean = f"{downloadDir}/{filenameDisk}"

        if extention != "":
            return f"{targetPathClean}{extention}"

        return f"{targetPathClean}{fileExtention}"

    def convertFile(self, sourceFile, targetFile):
        if (
            subprocess.run(
                f"ffmpeg -framerate 15 -i {sourceFile} -b:v 1M -c:v libvpx-vp9 -c:a libopus {targetFile}",
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=True,
            ).returncode
            != 0
        ):
            self.logger.debug(f"Error converting video. Check {sourceFile}")

        self.logger.debug(f"File successfully converted: {targetFile}")
        Path(sourceFile).unlink()
        self.logger.debug(f"Orginal file successfully deleted: {sourceFile}")

    def save_files(self, download_dir, files, blacklist=None, target_filetype=None):
        self.logger.debug(f"Start downloading files")

        for file in files:
            target_file_path = self.generateTargetFilePath(
                file["FileName"], download_dir
            )

            target_file_path_convert = None
            if target_filetype:
                target_file_path_convert = self.generateTargetFilePath(
                    file["FileName"], download_dir, extention=f"{target_filetype}"
                )

            if Path(f"{target_file_path}").is_file():
                self.logger.debug(f"File already exists: {target_file_path}")
                continue

            if (
                target_file_path_convert
                and Path(f"{target_file_path_convert}").is_file()
            ):
                self.logger.debug(
                    f"Converted file already exists: {target_file_path_convert}"
                )
                continue

            if blacklist:
                if target_file_path in blacklist:
                    self.logger.debug(f"File is on the blacklist: {target_file_path}")
                    continue
                if target_file_path_convert and target_file_path_convert in blacklist:
                    self.logger.debug(
                        f"File is on the blacklist: {target_file_path_convert}"
                    )
                    continue

            self.logger.debug(f"Downloading {target_file_path}...")
            self.cam.download_file(
                file["BeginTime"], file["EndTime"], file["FileName"], target_file_path
            )
            self.logger.debug(f"Finished downloading {target_file_path}...")

            if target_file_path_convert:
                self.logger.debug(f"Converting {target_file_path_convert}...")
                self.convertFile(target_file_path, target_file_path_convert)
                self.logger.debug(f"Finished converting {target_file_path_convert}.")

        self.logger.debug(f"Finish downloading files")

    def move_cam(self, direction, step=5):
        match direction:
            case "up":
                self.cam.ptz_step("DirectionUp", step=step)
            case "down":
                self.cam.ptz_step("DirectionDown", step=step)
            case "left":
                self.cam.ptz_step("DirectionLeft", step=step)
            case "right":
                self.cam.ptz_step("DirectionRight", step=step)
            case _:
                self.logger.debug(f"No direction found")

    def mute_cam(self):
        print(
            self.cam.send(
                1040,
                {
                    "fVideo.Volume": [
                        {"AudioMode": "Single", "LeftVolume": 0, "RightVolume": 0}
                    ],
                    "Name": "fVideo.Volume",
                },
            )
        )

    def set_volume(self, volume):
        print(
            self.cam.send(
                1040,
                {
                    "fVideo.Volume": [
                        {
                            "AudioMode": "Single",
                            "LeftVolume": volume,
                            "RightVolume": volume,
                        }
                    ],
                    "Name": "fVideo.Volume",
                },
            )
        )

    def get_battery(self):
        data = self.cam.send_custom(
            1610,
            {"Name": "OPTUpData", "OPTUpData": {"UpLoadDataType": 5}},
            size=260,
        )[87:-2].decode("utf-8")
        json_data = json.loads(data)
        return {
            "BatteryPercent": json_data["Dev.ElectCapacity"]["percent"],
            "Charging": json_data["Dev.ElectCapacity"]["electable"],
        }

    def get_storage(self):
        # get available storage in gb
        storage_result = []
        data = self.cam.send(1020, {"Name": "StorageInfo"})
        for storage_index, storage in enumerate(data["StorageInfo"]):
            for partition_index, partition in enumerate(storage["Partition"]):
                s = {
                    "Storage": storage_index,
                    "Partition": partition_index,
                    "RemainingSpace": int(partition["RemainSpace"], 0) / 1024,
                    "TotalSpace": int(partition["TotalSpace"], 0) / 1024,
                }
                storage_result.append(s)
        return storage_result
