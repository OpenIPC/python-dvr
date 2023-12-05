from time import sleep, monotonic
from dvrip import DVRIPCam, SomethingIsWrongWithCamera
from pathlib import Path
import logging


class NVR:
    nvr = None
    logger = None

    def __init__(self, host_ip, user, password, logger):
        self.logger = logger
        self.nvr = DVRIPCam(
            host_ip,
            user=user,
            password=password,
        )
        if logger.level <= logging.DEBUG:
            self.nvr.debug()

    def login(self):
        try:
            self.logger.info(f"Connecting to NVR...")
            self.nvr.login()
            self.logger.info("Successfuly connected to NVR.")
            return
        except SomethingIsWrongWithCamera:
            self.logger.error("Can't connect to NVR")
            self.nvr.close()

    def logout(self):
        self.nvr.close()

    def get_channel_statuses(self):
        channel_statuses = self.nvr.get_channel_statuses()
        if 'Ret' in channel_statuses:
            return None

        channel_titles = self.nvr.get_channel_titles()
        if 'Ret' in channel_titles:
            return None
        
        for i in range(min(len(channel_statuses), len(channel_titles))):
            channel_statuses[i]['Title'] = channel_titles[i]
            channel_statuses[i]['Channel'] = i

        return [c for c in channel_statuses if c['Status'] != '']

    def get_local_files(self, channel, start, end, filetype):
        return self.nvr.list_local_files(start, end, filetype, channel)

    def generateTargetFileName(self, filename):
        # My NVR's filename example: /idea0/2023-11-19/002/05.38.58-05.39.34[M][@69f17][0].h264
        # You should check file names in your NVR and review the transformation
        filenameSplit = filename.replace("][", "/").replace("[", "/").replace("]", "/").split("/")
        return f"{filenameSplit[3]}_{filenameSplit[2]}_{filenameSplit[4]}{filenameSplit[-1]}"

    def save_files(self, download_dir, files):
        self.logger.info(f"Files downloading: start")

        size_to_download = sum(int(f['FileLength'], 0) for f in files)

        for file in files:
            target_file_name = self.generateTargetFileName(file["FileName"])
            target_file_path = f"{download_dir}/{target_file_name}"

            size = int(file['FileLength'], 0)
            size_to_download -= size

            if Path(f"{target_file_path}").is_file():
                self.logger.info(f"  {target_file_name}  file already exists, skipping download")
                continue

            self.logger.info(f"  {target_file_name}  [{size/1024:.1f} MBytes] downloading...")
            time_dl = monotonic()
            self.nvr.download_file(
                file["BeginTime"], file["EndTime"], file["FileName"], target_file_path
            )
            time_dl = monotonic() - time_dl
            speed = size / time_dl
            self.logger.info(f"    Done [{speed:.1f} KByte/s]  {size_to_download/1024:.1f} MBytes more to download")

        self.logger.info(f"Files downloading: done")

    def list_files(self, files):
        self.logger.info(f"Files listing: start")

        for file in files:
            target_file_name = self.generateTargetFileName(file["FileName"])

            size = int(file['FileLength'], 0)
            self.logger.info(f"  {target_file_name} [{size/1024:.1f} MBytes]")

        self.logger.info(f"Files listing: end")
