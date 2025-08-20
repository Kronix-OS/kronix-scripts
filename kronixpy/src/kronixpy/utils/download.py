import sys
import tqdm
import requests
import io
import ftplib
from typing import Optional
from .errprint import perror
from . import unreachable
from os import PathLike

type StrOrBytesPath = str | bytes | PathLike[str] | PathLike[bytes]


def from_http(
    url: str, to: Optional[StrOrBytesPath] = None, showstatus: bool = False
) -> bytes | None:
    response = requests.get(url, stream=True)
    fsize = int(response.headers.get("content-length", 0))
    block_size = 1024
    if to is not None:
        with open(to, "w") as f:
            with tqdm.tqdm(
                total=fsize,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                disable=not showstatus,
                desc=f"downloading {url}",
            ) as status:
                for data in response.iter_content(block_size):
                    written = f.write(data)
                    status.update(written)
            return None
    else:
        with io.BytesIO() as f:
            with tqdm.tqdm(
                total=fsize,
                unit="B",
                unit_scale=True,
                unit_divisor=1024,
                disable=not showstatus,
                desc=f"downloading {url}",
            ) as status:
                for data in response.iter_content(block_size):
                    written = f.write(data)
                    status.update(written)
            return f.getvalue()
    return unreachable()


def from_ftp(
    url: str, to: Optional[StrOrBytesPath] = None, showstatus: bool = False
) -> bytes | None:
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

        fsize = ftp.size(file)

        if to is not None:
            with open(to, "w") as f:
                with tqdm.tqdm(
                    total=fsize,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    disable=not showstatus,
                    desc=f"downloading {url}",
                ) as status:

                    def _cb(b: bytes) -> int:
                        ret = f.write(b)
                        status.update(ret)
                        return ret

                    ftp.retrbinary("RETR " + file, _cb)
                return None
        else:
            with io.BytesIO() as f:
                with tqdm.tqdm(
                    total=fsize,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    disable=not showstatus,
                    desc=f"downloading {url}",
                ) as status:

                    def _cb(b: bytes) -> int:
                        ret = f.write(b)
                        status.update(ret)
                        return ret

                    ftp.retrbinary("RETR " + file, _cb)
                return f.getvalue()
    return unreachable()
