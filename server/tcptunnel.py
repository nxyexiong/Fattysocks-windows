import asyncore
import socket

from utils import LOGGER
from constants import BUF_SIZE, STAGE_HANDSHAKE, STAGE_STREAM, STAGE_INIT, TOKEN_LEN, PACK_SIZE_RAW, PACK_SIZE_ENCRYPT
from cipher import AESCipher


class TCPServer(asyncore.dispatcher):

    conn_list = []

    def __init__(self, host, port, key):
        asyncore.dispatcher.__init__(self)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.set_reuse_addr()
        self.bind((host, port))
        self.listen(5)
        self.key = key

    def handle_accept(self):
        pair = self.accept()
        if pair is not None:
            sock, addr = pair
            LOGGER.info('incoming connection from %s', repr(addr))
            handler = LocalConnection(sock, self.key)
            handler.server = self
            self.conn_list.append(handler)


class LocalConnection(asyncore.dispatcher):

    buffer_send = b''
    buffer_send_raw = b''
    buffer_recv = b''
    buffer_recv_raw = b''
    server = None
    remote = None
    stage = STAGE_INIT
    cipher = None

    def __init__(self, sock, key):
        super().__init__(sock)
        self.cipher = AESCipher(key)

    def handle_read(self):
        data = self.recv(BUF_SIZE)
        if not data:
            return
        self.buffer_recv += data
        while len(self.buffer_recv) >= PACK_SIZE_ENCRYPT:
            self.buffer_recv_raw += self.cipher.decrypt(self.buffer_recv[0:PACK_SIZE_ENCRYPT])
            self.buffer_recv = self.buffer_recv[PACK_SIZE_ENCRYPT:]

        #LOGGER.debug('%s local recv %s', id(self), data)
        while True:
            if self.stage == STAGE_INIT:
                if len(self.buffer_recv_raw) < TOKEN_LEN:
                    return
                token = self.buffer_recv_raw[0:TOKEN_LEN]
                LOGGER.info("accept token: %s", token.hex())
                self.buffer_recv_raw = self.buffer_recv_raw[TOKEN_LEN:]
                self.stage = STAGE_HANDSHAKE
                continue
            elif self.stage == STAGE_HANDSHAKE:
                if len(self.buffer_recv_raw) < 1:
                    return
                atyp = self.buffer_recv_raw[0]
                addr = ""
                port = 0
                if atyp == 1:
                    if len(self.buffer_recv_raw) < 7:
                        return
                    addr = str(self.buffer_recv_raw[1]) + '.' + str(self.buffer_recv_raw[2]) + \
                        '.' + str(self.buffer_recv_raw[3]) + '.' + str(self.buffer_recv_raw[4])
                    port = self.buffer_recv_raw[5] * 256 + self.buffer_recv_raw[6]
                    self.buffer_recv_raw = self.buffer_recv_raw[7:]
                elif atyp == 3:
                    if len(self.buffer_recv_raw) < 2:
                        return
                    alen = self.buffer_recv_raw[1]
                    if len(self.buffer_recv_raw) < 2 + alen + 2:
                        return
                    addr = self.buffer_recv_raw[2:2 + alen].decode('utf-8')
                    port = self.buffer_recv_raw[2 + alen] * 256 + self.buffer_recv_raw[2 + alen + 1]
                    self.buffer_recv_raw = self.buffer_recv_raw[2 + alen + 2:]
                LOGGER.info('%s local handshake: %s:%d', id(self), addr, port)
                try:
                    self.remote = RemoteConnection(addr, port)
                except Exception:
                    LOGGER.error('%s cannot connect to %s:%d', id(self), addr, port)
                    return
                self.remote.local = self
                self.remote.buffer += self.buffer_recv_raw
                self.buffer_recv_raw = b''
                self.stage = STAGE_STREAM
                continue
            elif self.stage == STAGE_STREAM:
                self.remote.buffer += self.buffer_recv_raw
                self.buffer_recv_raw = b''
            return

    def writable(self):
        while len(self.buffer_send_raw) > 0:
            data = None
            if len(self.buffer_send_raw) >= PACK_SIZE_RAW:
                data = self.buffer_send_raw[0:PACK_SIZE_RAW]
                self.buffer_send_raw = self.buffer_send_raw[PACK_SIZE_RAW:]
            else:
                data = self.buffer_send_raw
                self.buffer_send_raw = b''
            self.buffer_send += self.cipher.encrypt(data)
        return (len(self.buffer_send) > 0 and self.stage == STAGE_STREAM)

    def handle_write(self):
        sent = self.send(self.buffer_send)
        #LOGGER.debug('%s local send %s', id(self), self.buffer[:sent])
        self.buffer_send = self.buffer_send[sent:]

    def handle_close(self):
        LOGGER.info('%s local close', id(self))
        if self in self.server.conn_list:
            self.server.conn_list.remove(self)
        self.close()


class RemoteConnection(asyncore.dispatcher):

    buffer = b''
    local = None

    def __init__(self, host, port):
        asyncore.dispatcher.__init__(self)
        self.create_socket(socket.AF_INET, socket.SOCK_STREAM)
        self.connect((host, port))

    def handle_connect(self):
        pass

    def handle_close(self):
        LOGGER.info('%s remote close', id(self))
        if self.local:
            self.local.handle_close()
        self.close()

    def handle_read(self):
        data = self.recv(BUF_SIZE)
        #LOGGER.debug('%s remote recv: %s', id(self), data)
        self.local.buffer_send_raw += data

    def writable(self):
        return (len(self.buffer) > 0)

    def handle_write(self):
        sent = self.send(self.buffer)
        #LOGGER.debug('%s remote send %s', id(self), self.buffer[:sent])
        self.buffer = self.buffer[sent:]
