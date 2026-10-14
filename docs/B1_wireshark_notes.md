\# Wireshark verification — TLS



\## Setup

1\. Set `SSLKEYLOGFILE=%CD%\\certs\\sslkeys.log` before starting Python.

2\. In Wireshark: Preferences → Protocols → TLS → (Pre)-Master-Secret log filename = `certs\\sslkeys.log`.

3\. Run the security tests: `pytest tests\\security -v`.

4\. Filter: `tcp.port == <coordinator port>`.



\## What to check

\- ClientHello with SNI = "coordinator".

\- TLS handshake, then Application Data records.

\- Without SSLKEYLOGFILE: Application Data is opaque bytes.

\- With SSLKEYLOGFILE: Wireshark decrypts and shows the framed Envelope.



\## Evidence

\- Screenshot 1: opaque Application Data (proves encryption).

\- Screenshot 2: decrypted view showing Envelope fields (proves it's our protocol).

