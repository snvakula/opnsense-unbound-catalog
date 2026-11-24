# Security

Report suspected security issues privately through the repository's GitHub
security advisory interface when available. Keep credentials, real
infrastructure addresses, configuration backups and packet captures out of
public reports.

The 0.1.0 package exposes its controls through the native OPNsense MVC ACL and
configd. The foundation worker runs as `nobody`. It opens no listener or network
connection. Service actions, template generation and package hooks run with the
permissions of their native OPNsense entry points. Runtime actions take fixed
arguments. The generated rc file contains only explicit allowlisted values.
