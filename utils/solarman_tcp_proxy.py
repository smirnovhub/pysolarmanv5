"""Modbus TCP to Solarman proxy

Can be used with Home Assistant's native Modbus integration using config below:

- name: "solarman-modbus-proxy"
  type: tcp
  host: 192.168.1.20
  port: 1502
  delay: 3
  retry_on_empty: true
  sensors:
    [...]

"""

import argparse
import asyncio
import struct
import logging
from functools import partial
from umodbus.client.serial.redundancy_check import get_crc
from pysolarmanv5 import PySolarmanV5Async

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("solarman")

async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    logger_address: str,
    logger_serial: int,
):
    solarmanv5 = PySolarmanV5Async(
        address=logger_address, serial=logger_serial, verbose=True, auto_reconnect=True, logger=log
    )
    await solarmanv5.connect()

    addr = writer.get_extra_info("peername")

    log.info(f"{addr}: New connection")

    try:
        while True:
            try:
                # read Modbus TCP frame
                header = await reader.readexactly(6)
                # decode header
                trans_id, proto_id, length = struct.unpack(">HHH", header)
                unit_id = await reader.readexactly(1)
                pdu = await reader.readexactly(length - 1)  # length includes unit_id
            except asyncio.IncompleteReadError:
                # connection closed
                break

            # create equivalent RTU frame
            slave_id = b"\x01"
            modbus_rtu = slave_id + pdu + get_crc(slave_id + pdu)

            reply_rtu = await solarmanv5.send_raw_modbus_frame(modbus_rtu)

            # slave_id_reply = reply_rtu[0:1]
            pdu_reply = reply_rtu[1:-2]
            # crc_reply = reply_rtu[-2:]

            # Convert RTU back to TCP
            mbap = struct.pack(">HHH", trans_id, 0, len(pdu_reply) + 1)
            reply_tcp = mbap + unit_id + pdu_reply

            writer.write(reply_tcp)

        await writer.drain()
    except OSError:
        # https://github.com/python/cpython/issues/83037
        pass

    log.info(f"{addr}: Connection closed")
    await solarmanv5.disconnect()


async def run_proxy(
    bind_address: str, port: int, logger_address: str, logger_serial: int
):
    server = await asyncio.start_server(
        partial(
            handle_client, logger_address=logger_address, logger_serial=logger_serial
        ),
        bind_address,
        port,
    )
    async with server:
        log.info(f"Listening on {bind_address}:{port}")
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(
        prog="solarman-tcp-proxy",
        description="A Modbus TCP Proxy for Solarman loggers",
    )
    parser.add_argument(
        "-b", "--bind", default="0.0.0.0", help="The address to listen on"
    )
    parser.add_argument(
        "-p", "--port", default=1502, type=int, help="The TCP port to listen on"
    )
    parser.add_argument(
        "-l", "--logger", required=True, help="The IP address of the logger"
    )
    parser.add_argument(
        "-s", "--serial", required=True, type=int, help="The serial number of the logger"
    )
    args = parser.parse_args()

    asyncio.run(run_proxy(args.bind, args.port, args.logger, args.serial))


if __name__ == "__main__":
    main()
