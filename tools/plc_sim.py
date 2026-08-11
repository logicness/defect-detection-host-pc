"""
Modbus TCP 从站模拟器（供通信设置页联调）
默认监听 0.0.0.0:2000，地址映射与 core/plc_client.py 一致:
  线圈: 0=产线运行(ON) 1=故障(OFF) 2=剔除触发(写) 3=剔除确认(ON)
  寄存器: 0=合格计数 1=缺陷计数
用法: python tools/plc_sim.py [port]
"""
import socket
import struct
import sys
import threading

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 2000

coils = [True, False, False, True]
regs = [995, 5]


def handle(conn):
    buf = b""
    while True:
        data = conn.recv(4096)
        if not data:
            break
        buf += data
        while len(buf) >= 7:
            txid, proto, ln, unit = struct.unpack(">HHHB", buf[:7])
            if len(buf) < 7 + ln - 1:
                break
            pdu = buf[7:7 + ln - 1]
            buf = buf[7 + ln - 1:]
            fc = pdu[0]
            resp = b""
            if fc == 0x01:                      # 读线圈
                addr, n = struct.unpack(">HH", pdu[1:5])
                bits = bytearray((n + 7) // 8)
                for i in range(n):
                    if addr + i < len(coils) and coils[addr + i]:
                        bits[i // 8] |= 1 << (i % 8)
                resp = struct.pack("BB", fc, len(bits)) + bytes(bits)
            elif fc == 0x03:                    # 读寄存器
                addr, n = struct.unpack(">HH", pdu[1:5])
                vals = [regs[addr + i] if addr + i < len(regs) else 0
                        for i in range(n)]
                resp = struct.pack("BB", fc, n * 2) + struct.pack(f">{n}H", *vals)
            elif fc == 0x05:                    # 写单线圈
                addr, val = struct.unpack(">HH", pdu[1:5])
                if addr < len(coils):
                    coils[addr] = val == 0xFF00
                    if addr == 2 and val == 0xFF00:
                        regs[1] += 1            # 剔除一次 → 缺陷计数+1
                resp = pdu
            else:
                resp = bytes([fc | 0x80, 0x01])
            mbap = struct.pack(">HHHB", txid, 0, len(resp) + 1, unit)
            conn.sendall(mbap + resp)


def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(4)
    print(f"PLC 模拟器监听 :{PORT} (running={coils[0]}, pass={regs[0]}, defect={regs[1]})")
    while True:
        conn, addr = srv.accept()
        print(f"上位机连接: {addr}")
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
