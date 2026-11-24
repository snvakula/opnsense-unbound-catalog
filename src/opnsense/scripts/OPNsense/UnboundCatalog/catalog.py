"""Bounded RFC 9432 version 2 catalog transfers."""

import ipaddress
import re
import socket
import struct
import time
from dataclasses import dataclass

import dns.flags
import dns.message
import dns.name
import dns.opcode
import dns.rcode
import dns.rdataclass
import dns.rdatatype


class CatalogError(ValueError):
    """An invalid or unsupported catalog."""


def dns_name(value):
    name = (dns.name.from_text(value) if isinstance(value, str) else value).canonicalize()
    if not name.is_absolute() or name == dns.name.root or not all(
        re.fullmatch(rb"[a-z0-9_][a-z0-9_-]{0,62}", label) for label in name.labels[:-1]
    ):
        raise CatalogError("Unsupported DNS name")
    return name


@dataclass(frozen=True)
class Snapshot:
    serial: int
    members: dict[str, str]


class CatalogTransfer:
    def __init__(self, name, max_records):
        self.name = dns_name(name)
        self.query = dns.message.make_query(self.name, dns.rdatatype.AXFR)
        self.query.flags = 0
        self.max_records = max_records
        self.records = 0
        self.soa = None
        self.closed = False
        self.has_ns = False
        self.versions = []
        self.members = {}
        self.targets = set()
        self.members_root = dns.name.from_text("zones", origin=self.name)
        self.version_name = dns.name.from_text("version", origin=self.name)

    def feed(self, message):
        if (
            message.id != self.query.id
            or not message.flags & dns.flags.QR
            or message.flags & dns.flags.TC
            or message.opcode() != dns.opcode.QUERY
            or message.rcode() != dns.rcode.NOERROR
            or (message.question and message.question != self.query.question)
            or (not self.soa and message.question != self.query.question)
            or message.authority
            or not message.answer
        ):
            raise CatalogError("Invalid AXFR response")

        for rrset in message.answer:
            name = rrset.name.canonicalize()
            if rrset.rdclass != dns.rdataclass.IN or not name.is_subdomain(self.name):
                raise CatalogError("Record lies outside the requested IN zone")
            for record in rrset:
                self.records += 1
                if self.records > self.max_records:
                    raise CatalogError("AXFR record limit exceeded")
                if self.closed:
                    raise CatalogError("Records follow the closing SOA")
                if not self.soa or record.rdtype == dns.rdatatype.SOA:
                    self._soa(name, record, rrset.ttl)
                else:
                    self._record(name, record)

    def _soa(self, name, record, ttl):
        if name != self.name or record.rdtype != dns.rdatatype.SOA:
            raise CatalogError("AXFR must open with an apex SOA")
        if self.soa is None:
            self.soa = (record, ttl)
        elif self.soa != (record, ttl):
            raise CatalogError("Opening and closing SOA differ")
        else:
            self.closed = True

    def _record(self, name, record):
        kind = record.rdtype
        if name == self.name and kind == dns.rdatatype.NS:
            self.has_ns = True
        if name == self.version_name and kind == dns.rdatatype.TXT:
            self.versions.append(record.strings)
        if name.is_subdomain(self.members_root):
            labels = name.relativize(self.members_root).labels
            if len(labels) == 1 and kind == dns.rdatatype.PTR:
                member = dns_name(record.target).to_text()
                if name in self.members or member in self.targets or member == self.name.to_text():
                    raise CatalogError("Duplicate or self-referencing catalog member")
                self.members[name] = member
                self.targets.add(member)
            elif len(labels) == 2 and labels[0] == b"coo" and kind == dns.rdatatype.PTR:
                raise CatalogError("Catalog change of ownership is unsupported")
        # RFC 9432 requires ignoring unknown group values and custom properties.

    def finish(self):
        if not self.closed or not self.has_ns or self.versions != [(b"2",)]:
            raise CatalogError("Incomplete AXFR or invalid RFC 9432 version marker")
        return Snapshot(
            self.soa[0].serial,
            {name.relativize(self.members_root).to_text(): member for name, member in self.members.items()},
        )


def _read_exact(connection, count, deadline):
    result = bytearray()
    while len(result) < count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("AXFR lifetime exceeded")
        connection.settimeout(remaining)
        chunk = connection.recv(count - len(result))
        if not chunk:
            raise CatalogError("Transfer ended before the closing SOA")
        result.extend(chunk)
    return bytes(result)


def transfer(name, server, port, timeout, max_records, max_bytes):
    """Request an unsigned full transfer from a numeric IPv4 or IPv6 address."""
    deadline = time.monotonic() + timeout
    receiver = CatalogTransfer(name, max_records)
    query = receiver.query.to_wire()
    received = 0
    with socket.create_connection((str(ipaddress.ip_address(server)), port), timeout) as connection:
        connection.settimeout(max(0.001, deadline - time.monotonic()))
        connection.sendall(struct.pack("!H", len(query)) + query)
        while not receiver.closed:
            size, = struct.unpack("!H", _read_exact(connection, 2, deadline))
            received += size + 2
            if received > max_bytes:
                raise CatalogError("AXFR wire-byte limit exceeded")
            wire = _read_exact(connection, size, deadline)
            receiver.feed(dns.message.from_wire(wire, xfr=True, one_rr_per_rrset=True))
    return receiver.finish()
