"""Authorised RFC 1996 UDP notifications."""

import ipaddress
import selectors
import socket
import time
from collections import OrderedDict
from threading import Event, Thread

import dns.flags
import dns.message
import dns.opcode
import dns.rcode
import dns.rdataclass
import dns.rdatatype
from catalog import CatalogError, dns_name
from dns.exception import DNSException

MAX_DATAGRAM = 1232
MAX_SOURCES = 1024


class RateLimit:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.global_bucket = (clock(), 100)
        self.sources = OrderedDict()

    @staticmethod
    def consume(bucket, now, rate, burst):
        timestamp, tokens = bucket
        tokens = min(burst, tokens + (now - timestamp) * rate)
        return (now, max(0, tokens - 1) if tokens >= 1 else tokens), tokens >= 1

    def allow(self, source):
        now = self.clock()
        self.global_bucket, allowed = self.consume(self.global_bucket, now, 100, 100)
        if not allowed:
            return False
        previous = self.sources.pop(source, (now, 10))
        self.sources[source], allowed = self.consume(previous, now, 5, 10)
        if len(self.sources) > MAX_SOURCES:
            self.sources.popitem(last=False)
        return allowed


class Notify:
    def __init__(self, poller):
        self.poller = poller
        self.log = poller.log
        self.limiter = RateLimit()
        self.last_rejection = float("-inf")
        self.allowlists = {
            name: catalog.sources for name, catalog in poller.settings.catalogs.items()
        }
        self.sources = tuple({network for networks in self.allowlists.values() for network in networks})

    def reject(self, reason):
        now = time.monotonic()
        if now - self.last_rejection >= 60:
            self.log("warning", f"DNS NOTIFY dropped: {reason}")
            self.last_rejection = now

    def receive(self, wire, host):
        source = ipaddress.ip_address(host.split("%", 1)[0])
        if not any(source in network for network in self.sources):
            self.reject("unauthorized source")
            return None
        if not self.limiter.allow(source):
            self.reject("rate limit")
            return None
        try:
            if len(wire) > MAX_DATAGRAM:
                raise CatalogError("datagram limit")
            request = dns.message.from_wire(wire)
            if (
                request.opcode() != dns.opcode.NOTIFY
                or request.flags & ~(dns.flags.AA | 0x7800)
                or not request.flags & dns.flags.AA
                or request.rcode() != dns.rcode.NOERROR
                or len(request.question) != 1
                or request.authority
                or request.additional
            ):
                raise CatalogError("invalid NOTIFY header")
            question = request.question[0]
            zone = dns_name(question.name).to_text()
            if question.rdclass != dns.rdataclass.IN or question.rdtype != dns.rdatatype.SOA:
                raise CatalogError("NOTIFY requires an IN SOA question")
            if request.answer and (
                len(request.answer) != 1 or len(request.answer[0]) != 1
                or request.answer[0].name != question.name
                or request.answer[0].rdclass != question.rdclass
                or request.answer[0].rdtype != question.rdtype
            ):
                raise CatalogError("invalid NOTIFY SOA answer")
        except (CatalogError, DNSException) as error:
            self.reject(error)
            return None

        catalog = zone in self.allowlists
        owners = (zone,) if catalog else self.poller.member_owners.get(zone, ())
        if owners and not any(source in net for owner in owners for net in self.allowlists[owner]):
            self.reject("source not allowed for zone")
            return None

        response = dns.message.make_response(request)
        if not owners:
            response.set_rcode(dns.rcode.REFUSED)
        else:
            response.flags |= dns.flags.AA
            self.log("debug", f"{zone} authorized DNS NOTIFY")
            if catalog:
                self.poller.request(zone)
        return response.to_wire()


class Listener:
    def __init__(self, poller):
        self.notify = Notify(poller)
        self.settings = poller.settings
        self.log = poller.log
        self.stop = Event()
        self.selector = selectors.DefaultSelector()
        self.thread = None
        self.sockets = []

    def __enter__(self):
        if not self.settings.notify_enabled:
            return self
        for family, enabled, address in [
            (socket.AF_INET, self.settings.ipv4, "0.0.0.0"),  # nosec B104 - RFC 1996 listener with a source allowlist
            (socket.AF_INET6, self.settings.ipv6, "::"),
        ]:
            if not enabled:
                continue
            try:
                sock = socket.socket(family, socket.SOCK_DGRAM)
                self.sockets.append(sock)
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                sock.bind((address, self.settings.notify_port))
                sock.setblocking(False)
                self.selector.register(sock, selectors.EVENT_READ)
                self.log("info", f"DNS NOTIFY listening on {address} port {self.settings.notify_port}")
            except OSError as error:
                self.log("error", f"DNS NOTIFY bind failed on {address}: {error}")
        if self.selector.get_map():
            self.thread = Thread(target=self._listen, name="dns-notify")
            self.thread.start()
        return self

    def _listen(self):
        while not self.stop.is_set():
            for key, _ in self.selector.select(0.25):
                sock = key.fileobj
                try:
                    wire, source = sock.recvfrom(MAX_DATAGRAM + 1)
                    response = self.notify.receive(wire, source[0])
                    if response is not None:
                        sock.sendto(response, source)
                except OSError as error:
                    self.notify.reject(error)

    def __exit__(self, *_exc):
        self.stop.set()
        if self.thread:
            self.thread.join()
        self.selector.close()
        for sock in self.sockets:
            sock.close()
