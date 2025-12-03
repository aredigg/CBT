import os
import shutil
import sys

from . import Config
from .channels import Channels


def main(args: list[str]) -> int:
    Config.load()
    Config.set_args(args)
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
    if channels.status() > 0:
        print(channels.status_message())
    return channels.status()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
