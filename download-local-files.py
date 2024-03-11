from pathlib import Path
from time import sleep
import os
import json
import logging
from collections import namedtuple
from solarcam import SolarCam


def init_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


def load_config():
    def config_decoder(config_dict):
        return namedtuple("X", config_dict.keys())(*config_dict.values())

    config_path = os.environ.get("CONFIG_PATH")
    if Path(config_path).exists():
        with open(config_path, "r") as file:
            return json.loads(file.read(), object_hook=config_decoder)

    return {
        "host_ip": os.environ.get("IP_ADDRESS"),
        "user": os.environ.get("USER"),
        "password": os.environ.get("PASSWORD"),
        "target_filetype_video": os.environ.get("target_filetype_video"),
        "download_dir_video": os.environ.get("DOWNLOAD_DIR_VIDEO"),
        "download_dir_picture": os.environ.get("DOWNLOAD_DIR_PICTURE"),
        "start": os.environ.get("START"),
        "end": os.environ.get("END"),
        "blacklist_path": os.environ.get("BLACKLIST_PATH"),
        "cooldown": int(os.environ.get("COOLDOWN")),
        "dump_local_files": (
            os.environ.get("DUMP_LOCAL_FILES").lower() in ["true", "1", "y", "yes"]
        ),
    }


def main():
    logger = init_logger()
    config = load_config()
    start = config.start
    end = config.end
    cooldown = config.cooldown

    blacklist = None
    if Path(config.blacklist_path).exists():
        with open(config.blacklist_path, "r") as file:
            blacklist = [line.rstrip() for line in file]

    while True:
        solarCam = SolarCam(config.host_ip, config.user, config.password, logger)

        try:
            solarCam.login()

            battery = solarCam.get_battery()
            logger.debug(f"Current battery status: {battery}")
            storage = solarCam.get_storage()[0]
            logger.debug(f"Current storage status: {storage}")

            logger.debug(f"Syncing time...")
            solarCam.set_time()  # setting it to system clock
            logger.debug(f"Camera time is now {solarCam.get_time()}")

            sleep(5)  # sleep some seconds so camera can get ready

            pics = solarCam.get_local_files(start, end, "jpg")

            if pics:
                Path(config.download_dir_picture).parent.mkdir(
                    parents=True, exist_ok=True
                )
                solarCam.save_files(
                    config.download_dir_picture, pics, blacklist=blacklist
                )

            videos = solarCam.get_local_files(start, end, "h264")
            if videos:
                Path(config.download_dir_video).parent.mkdir(
                    parents=True, exist_ok=True
                )
                solarCam.save_files(
                    config.download_dir_video,
                    videos,
                    blacklist=blacklist,
                    target_filetype=config.target_filetype_video,
                )

            if config.dump_local_files:
                logger.debug(f"Dumping local files...")
                solarCam.dump_local_files(
                    videos,
                    config.blacklist_path,
                    config.download_dir_video,
                    target_filetype=config.target_filetype_video,
                )
                solarCam.dump_local_files(
                    pics, config.blacklist_path, config.download_dir_picture
                )

            solarCam.logout()
        except ConnectionRefusedError:
            logger.debug(f"Connection could not be established or got disconnected")
        except TypeError as e:
            print(e)
            logger.debug(f"Error while downloading a file")
        except KeyError:
            logger.debug(f"Error while getting the file list")
        logger.debug(f"Sleeping for {cooldown} seconds...")
        sleep(cooldown)


if __name__ == "__main__":
    main()

# todo add flask api for moving cam
# todo show current stream
# todo show battery on webinterface and write it to mqtt topic
# todo change camera name
