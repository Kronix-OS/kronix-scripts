import sys
import requests
import io
import ftplib
from typing import Optional
from .errprint import perror
from . import unreachable
from os import PathLike

type StrOrBytesPath = str | bytes | PathLike[str] | PathLike[bytes]


def from_http(
    url: str, to: Optional[StrOrBytesPath] = None
) -> io.BytesIO | None:
    response = requests.get(url, stream=True)
    response.raise_for_status()
    assert response.ok
    block_size = 1024
    if to is not None:
        with open(to, "wb") as f:
            for data in response.iter_content(block_size):
                f.write(data)
            return None
    else:
        f = io.BytesIO()
        for data in response.iter_content(block_size):
            f.write(data)
        return f
    return unreachable()


def from_ftp(
    url: str, to: Optional[StrOrBytesPath] = None
) -> io.BytesIO | None:
    if not "://" in url:
        actual_host = url
    else:
        actual_host = url.split("://")[1]
    if "/" not in actual_host:
        raise ValueError("No file specified")
    file = actual_host.split("/")[-1]
    directory = "/".join(actual_host.split("/")[1:-1])
    if not directory:
        directory = "/"
    actual_host = actual_host.split("/")[0]
    with ftplib.FTP(actual_host) as ftp:
        ftp.login()
        ftp.cwd(directory)
        if to is not None:
            with open(to, "wb") as f:
                ftp.retrbinary("RETR " + file, lambda b: f.write(b))
                return None
        else:
            f = io.BytesIO()
            ftp.retrbinary("RETR " + file, lambda b: f.write(b))
            return f
    return unreachable()
