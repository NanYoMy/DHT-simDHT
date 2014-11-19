#encoding: utf-8
#！/usr/bin
#!/usr/bin/env python
import socket
import logging
from hashlib import sha1
from random import randint
from struct import unpack, pack
from socket import inet_aton, inet_ntoa
from threading import Timer, Thread
from time import sleep
from bencode import bencode, bdecode

stdger = logging.getLogger("std_log")
fileger = logging.getLogger("file_log")

BOOTSTRAP_NODES = [
    ("router.bittorrent.com", 6881),
    ("dht.transmissionbt.com", 6881),
    ("router.utorrent.com", 6881)
] 

TID_LENGTH = 4
RE_JOIN_DHT_INTERVAL = 10
THREAD_NUMBER = 3

def initialLog():

    stdLogLevel = logging.DEBUG
    fileLogLevel = logging.DEBUG
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(formatter)
    file_handler = logging.FileHandler("HASH.log")
    file_handler.setFormatter(formatter)
    logging.getLogger("file_log").setLevel(fileLogLevel)
    logging.getLogger("file_log").addHandler(file_handler)
    logging.getLogger("std_log").setLevel(stdLogLevel)
    logging.getLogger("std_log").addHandler(stdout_handler)

def entropy(length):
    chars = []
    for i in range(length):
        chars.append(chr(randint(0, 255)))
    return "".join(chars)

def random_id():
    hash = sha1()
    hash.update(entropy(20))
    return hash.digest()

def decode_nodes(nodes):
    n = []
    length = len(nodes)
    if (length % 26) != 0: 
        return n

    for i in range(0, length, 26):
        nid = nodes[i:i+20]
        ip = inet_ntoa(nodes[i+20:i+24])
        port = unpack("!H", nodes[i+24:i+26])[0]
        n.append((nid, ip, port))

    return n

def timer(t, f):
    Timer(t, f).start()

def get_neighbor(target, end=10):
    return target[:end]+random_id()[end:]


class DHT(Thread):
    def __init__(self, master, bind_ip, bind_port, max_node_qsize):
        Thread.__init__(self)

        self.setDaemon(True)
        self.isServerWorking = True
        self.isClientWorking = True
        self.master = master
        self.bind_ip = bind_ip
        self.bind_port = bind_port
        self.max_node_qsize = max_node_qsize
        self.table = KTable()
        self.ufd = socket.socket(socket.AF_INET,socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.ufd.bind((self.bind_ip, self.bind_port))
        self.server_thread=Thread(target=self.server)
        self.client_thread=Thread(target=self.client)
        self.server_thread.daemon=True
        self.client_thread.daemon=True

        timer(RE_JOIN_DHT_INTERVAL, self.join_DHT)

    def start(self):
        self.server_thread.start()
        self.client_thread.start()
        Thread.start(self)
        return self

    def server(self):
        self.join_DHT()
        while self.isServerWorking:

            try:
                (data, address) = self.ufd.recvfrom(65536)
                msg = bdecode(data)
                stdger.debug("receive udp packet")
                self.on_message(msg, address)
            except Exception:
                pass

    def client(self):
        while self.isClientWorking:

            for node in list(set(self.table.nodes))[:self.max_node_qsize]:
                stdger.debug("send packet")
                self.send_find_node((node.ip, node.port), node.nid)

            #is the list in python thread-safe
            size = len(self.table.nodes)
            head = size-self.max_node_qsize

            if head < 0:
                head = 0
            self.table.nodes = self.table.nodes[head : size]
            sleep(1)

    def on_message(self, msg, address):
        try:
            if msg["y"] == "r":
                if msg["r"].has_key("nodes"):
                    self.process_find_node_response(msg, address)

            elif msg["y"] == "q":
                if msg["q"] == "find_node":
                    self.process_find_node_request(msg, address)

                elif msg["q"] == "get_peers":
                    self.process_get_peers_request(msg, address)
        except KeyError, e:
            pass

    #send msg to a specified address
    def send_krpc(self, msg, address):
        try:
            self.ufd.sendto(bencode(msg), address)
        except:
            pass

    #send udp message
    def send_find_node(self, address, nid=None):
        nid = get_neighbor(nid) if nid else self.table.nid
        #token id
        tid = entropy(TID_LENGTH)
        #random_id() quite good idea
        msg = dict(
            t = tid,
            y = "q",
            q = "find_node",
            a = dict(id = nid, target = random_id())
        )
        self.send_krpc(msg, address)

    #only need to send a random_id to the bootstrap node.
    def join_DHT(self):
        for address in BOOTSTRAP_NODES: 
            self.send_find_node(address)



    def play_dead(self, tid, address):
        msg = dict(
            t = tid,
            y = "e",
            e = [202, "Server Error"]
        )
        self.send_krpc(msg, address)

    def process_find_node_response(self, msg, address):
        nodes = decode_nodes(msg["r"]["nodes"])
        for node in nodes:
            (nid, ip, port) = node
            if len(nid) != 20: continue
            if ip == self.bind_ip: continue
            self.table.put(KNode(nid, ip, port))

    def process_get_peers_request(self, msg, address):
        try:
            tid = msg["t"]
            infohash = msg["a"]["info_hash"]
            self.master.log(infohash, address)
            self.play_dead(tid, address)
        except KeyError, e:
            pass

    def process_find_node_request(self, msg, address):
        try:
            tid = msg["t"]
            target = msg["a"]["target"]
            self.master.log(target, address)
            self.play_dead(tid, address)
        except KeyError, e:
            pass

    def stop(self):
        self.isClientWorking = False
        self.isServerWorking = False

class KTable():
    def __init__(self):
        self.nid = random_id()
        self.nodes = []

    def put(self, node):
        self.nodes.append(node)


class KNode(object):
    def __init__(self, nid, ip=None, port=None):
        self.nid = nid
        self.ip = ip
        self.port = port

    def __eq__(self, node):
        return node.nid == self.nid

    def __hash__(self):
        return hash(self.nid)


#using example
class Master(object):

    def log(self, infohash, address=None):
        stdger.debug("%s from %s:%s" % (infohash.encode("hex"), address[0], address[1]))
        fileger.debug('%s from %s:%s' % (infohash.encode('hex').upper(),address[0],address[1]))


if __name__ == "__main__":
    #max_node_qsize bigger, bandwith bigger, spped higher
    initialLog()
    threads = []
    for i in xrange(THREAD_NUMBER):
        port = i+9500
        stdger.debug("start thread %d" % port)
        dht=DHT(Master(), "0.0.0.0", port, max_node_qsize=1000)
        dht.start()
        threads.append(dht)
        sleep(1)


    sleep(60*60*6)

    k = 0
    for i in threads:
        stdger.debug("stop thread %d" % k)
        i.stop()
        i.join()
        k=k+1
