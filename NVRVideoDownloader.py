from pathlib import Path
import os
import json
import logging
from collections import namedtuple
from NVR import NVR


def init_logger(log_level):
    logger = logging.getLogger(__name__)
    logger.setLevel(log_level)
    ch = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


def load_config():
    def config_decoder(config_dict):
        return namedtuple("X", config_dict.keys())(*config_dict.values())

    config_path = os.environ.get("NVRVIDEODOWNLOADER_CFG")

    if config_path is None or not Path(config_path).exists():
        config_path = "NVRVideoDownloader.json"

    if Path(config_path).exists():
        with open(config_path, "r") as file:
            return json.loads(file.read(), object_hook=config_decoder)

    return {
        "host_ip": os.environ.get("IP_ADDRESS"),
        "user": os.environ.get("USER"),
        "password": os.environ.get("PASSWORD"),
        "channel": os.environ.get("CHANNEL"),
        "download_dir": os.environ.get("DOWNLOAD_DIR"),
        "start": os.environ.get("START"),
        "end": os.environ.get("END"),
        "just_list_files": os.environ.get("DUMP_LOCAL_FILES").lower() in ["true", "1", "y", "yes"],
        "log_level": "INFO"
    }


def main():
    config = load_config()
    logger = init_logger(config.log_level)
    channel = config.channel;
    start = config.start
    end = config.end
    just_list_files = config.just_list_files;

    nvr = NVR(config.host_ip, config.user, config.password, logger)

    try:
        nvr.login()

        channel_statuses = nvr.get_channel_statuses()
        if channel_statuses:
            channel_statuses_short = [{f"{c['Channel']}:{c['Title']}({c['ChnName']})"}
                for c in channel_statuses if c['Status'] != 'NoConfig']
            logger.info(f"Configured channels in NVR: {channel_statuses_short}")

        videos = nvr.get_local_files(channel, start, end, "h264")
        if videos:
            size = sum(int(f['FileLength'], 0) for f in videos)
            logger.info(f"Video files found: {len(videos)}. Total size: {size/1024:.1f}M")
            Path(config.download_dir).parent.mkdir(
                parents=True, exist_ok=True
            )
            if just_list_files:
                nvr.list_files(videos)
            else:
                nvr.save_files(config.download_dir, videos)
        else:
            logger.info(f"No video files found")

        nvr.logout()
    except ConnectionRefusedError:
        logger.error(f"Connection can't be established or got disconnected")
    except TypeError as e:
        print(e)
        logger.error(f"Error while downloading a file")
    except KeyError:
        logger.error(f"Error while getting the file list")


if __name__ == "__main__":
    main()
