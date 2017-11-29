#!/usr/bin/python
# -*- coding: utf8 -*-

import gevent, gevent.server, gevent.monkey
gevent.monkey.patch_all()

import sys, time, os, logging, collections, traceback, config, database, proto, utils

from daemon import runner
from utils import Disconnect, BadClient

log = logging.getLogger("broker")

class Connection(object):
    def __init__(self, sock, addr, srv):
        self.sock = sock
        self.addr = addr
        self.srv = srv
        self.uid = None
        self.ak = None
        self.pubchans = []
        self.subchans = []
        self.active = True

    def __del__(self):
        # if this message is not showing up we're leaking references
        log.debug("Connection cleanup {0}".format(self.addr))

    def write(self, data):
        try:
            log.debug("Writing data to: {0}".format(self.addr))
            self.sock.sendall(data)
        except Exception as e:
            #traceback.print_exc()
            log.critical('Exception when writing to conn: {0}'.format(e))
        return

    def handle(self):
        # first send the info message
        self.authrand = authrand = os.urandom(4)
        log.debug("Authentication starting for: {0}".format(self.addr))
        self.write(proto.msginfo(config.FBNAME, authrand))

        self.mandatory_authentication()

        while True:
            opcode, ident, data = self.read_message()

            if not ident == self.ak:
                self.error("Invalid authkey in message.", ident=ident)
                log.critical("Invalid authkey in message from: {0}".format(ident))
                raise BadClient()

            if opcode == proto.OP_PUBLISH:
                chan, payload = proto.split(data, 1)

                if not self.may_publish(chan) or chan.endswith("..broker"):
                    self.error("Authkey not allowed to publish here.", chan=chan)
                    log.critical("Authkey not allowed ot publish here: {0}".format(chan))
                    continue

                self.srv.do_publish(self, chan, payload)

            elif opcode == proto.OP_SUBSCRIBE:
                chan = data
                checkchan = chan

                if chan.endswith('..broker'): checkchan = chan.rsplit('..broker', 1)[0]

                if not self.may_subscribe(checkchan):
                    self.error("Authkey not allowed to subscribe here.", chan=chan)
                    log.critical("Authkey not allowed ot publish here: {0}".format(chan))
                    continue

                self.srv.do_subscribe(self, ident, chan)

            elif opcode == proto.OP_UNSUBSCRIBE:
                chan = data
                self.do_unsubscribe(self, ident, chan)

            else:
                self.error("Unknown message type.", opcode=opcode, length=len(data))
                log.critical("Unknown message type: {0}".format(opcode))
                raise BadClient()

    def may_publish(self, chan):
        return chan in self.pubchans

    def may_subscribe(self, chan):
        return chan in self.subchans

    def mandatory_authentication(self):
        opcode, ident, rhash = self.read_message()
        if not opcode == proto.OP_AUTH:
            self.error("First message was not AUTH.")
            log.critical("First message was not AUTH")
            raise BadClient()

        self.authkey_check(ident, rhash)

    def authkey_check(self, ident, rhash):
        akrow = self.srv.get_authkey(ident)
        if not akrow:
            self.error("Authentication failed.", ident=ident)
            log.critical("Authentication failed for: {0}".format(ident))
            raise BadClient()

        akhash = utils.hash(self.authrand, akrow["secret"])

        if not akhash == rhash:
            self.error("Authentication failed.", ident=ident)
            log.critical("Authentication failed for: {0}".format(ident))
            raise BadClient()

        self.ak = ident
        self.uid = akrow["owner"]
        self.pubchans = akrow.get("pubchans", [])
        self.subchans = akrow.get("subchans", [])

    def read_message(self):
        return proto.read_message(self.sock)

    def forward(self, ident, chan, data):
        self.write(proto.msgpublish(ident, chan, data))

    def log(self, msg, *args):
        log.info(msg.format(*args))

    def error(self, msg, *args, **context):
        emsg = msg.format(*args)
#        log.critical(emsg)
        self.srv.log_error(emsg, self, context)
        self.write(proto.msgerror(emsg))


class Server(object):
    def __init__(self):
        self.listener = gevent.server.StreamServer((config.FBIP, config.FBPORT), self._newconn, **config.SSLOPTS)
        self.db = self.dbclass()
        self.connections = set()
        self.subscribermap = collections.defaultdict(list)
        self.conn2chans = collections.defaultdict(list)

    def dbclass(self):
        return database.Database()

    def connclass(self, *args):
        return Connection(*args)

    def serve_forever(self):
        log.info("StreamServer ssl_enabled=%s", str(self.listener.ssl_enabled))
        self.listener.serve_forever()

    def _newconn(self, sock, addr):
        log.debug('New connection from {0}.'.format(addr))
        fc = self.connclass(sock, addr, self)
        self.connections.add(fc)
        
        try: fc.handle()
        except Disconnect:
            log.debug("Connection closed by {0}".format(addr))
        except BadClient:
            log.warn('Connection ended because of bad client: {0}'.format(addr))

        fc.active = False

        for chan in self.conn2chans[fc]:
            self.subscribermap[chan].remove(fc)
            if fc.ak:
                self._brokerchan(fc, chan, fc.ak, 'leave')

        del self.conn2chans[fc]

        self.connections.remove(fc)
        try: sock.close()
        except: pass

    def do_publish(self, c, chan, data):
        log.info('Publish to {0} by {1} ak {2} addr {3}'.format(chan, c.uid, c.ak, c.addr))
        try:
            for c2 in self.receivers(chan, c, self.subscribermap[chan]):
                c2.forward(c.ak, chan, data)
        except Exception as e:
            traceback.print_exc()
        
    def do_subscribe(self, c, ident, chan):
        log.info('Broker subscribe to {0} by {1}@{2}'.format(chan, ident, c.addr))
        self.subscribermap[chan].append(c)
        self.conn2chans[c].append(chan)
        if not chan.endswith('..broker'):
            self._brokerchan(c, chan, ident, 'join')
    
    def do_unsubscribe(self, c, ident, chan):
        log.info('Broker unsubscribe to {0} by {1}@{2}'.format(chan, ident, c.addr))
        self.subscribermap[chan].remove(c)
        self.conn2chans[c].remove(chan)
        if not chan.endswith('..broker'):
            self._brokerchan(c, chan, ident, 'leave')

    def _brokerchan(self, c, chan, ident, data):
        brokchan = chan + '..broker'
        try:
            for c2 in self.receivers(chan, c, self.subscribermap[brokchan]):
                c2.publish(ident, chan, data)
        except Exception as e:
            traceback.print_exc()

    def log_error(self, emsg, conn, context):
        return self.db.log({
            "msg": emsg,
            "ip": conn.addr[0],
            "user": conn.uid,
            "authkey": conn.ak,
            "context": context,
        })

    def get_authkey(self, identifier):
        return self.db.get_authkey(identifier)

    def receivers(self, chan, conn, subscribed_conns):
        if not subscribed_conns: return

        # this is plain hpfeeds mode, no graph
        # all subscribed connections allowed to receive by default
        for c in subscribed_conns:
            yield c

class Broker(object):
    def __init__(self):
        self.stdin_path = '/dev/null'
        self.stdout_path = './logs/out.log'
        self.stderr_path = './logs/err.log'
        self.pidfile_path = '/tmp/broker.pid'
        self.pidfile_timeout = 5
        self.logfile = os.getcwd()+'/logs/broker.log'

    def run(self):
        logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                            filename=self.logfile,
                            level=logging.DEBUG if config.DEBUG else logging.INFO)
        try:
            while True:
                log.info("broker starting up...")
                s = Server()
                s.serve_forever()
        except (SystemExit,KeyboardInterrupt):
            pass
        except:
            log.exception("Exception")
        finally:
            log.info("broker shutting down...")

if __name__ == '__main__':

    broker_runner = runner.DaemonRunner(Broker())
    # Docker causes is_detach_process_context_required() to return False
    # Explicitly set detach_process to True (override None)
    broker_runner.daemon_context.detach_process=True
    broker_runner.do_action()

