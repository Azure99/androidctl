from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    USAGE = 2
    ENVIRONMENT = 3
