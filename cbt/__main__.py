import os
import shutil
import sys
import time

from . import Config
from .channels import Channels
from .debug import Debug


def main(args: list[str]) -> int:
    Config.load()
    Config.set_args(args)
    Debug.ready()
    try:
        if output_directory := Config.getstr("output_directory"):
            os.makedirs(output_directory, exist_ok=True)
        else:
            print("Output directory not set")
            return 2
        if temporary_storage := Config.getstr("temporary_storage"):
            shutil.rmtree(temporary_storage, ignore_errors=True)
            os.makedirs(temporary_storage, exist_ok=True)
        else:
            print("Temporary storage directory not set")
            return 2
    except PermissionError as e:
        print("Permission Error", e)
        return 1

    channels = Channels()
    channels.manager()
    for n in range(5, 0, -1):
        print(f"{n}...\033[0m\033[0K\033[1F")
        time.sleep(Config.KILL)
    if channels.status() > 0:
        print(channels.status_message())
    else:
        print("OK...\033[0m\033[0K\033[1F")
    Debug.close()
    return channels.status()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
