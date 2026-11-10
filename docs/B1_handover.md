\# B1 — Network Layer Handover



\## Overview

The B1 network layer uses TCP with Protocol Buffers for serialization. All traffic is secured with mutual TLS (mTLS). Nodes dial the coordinator and keep a persistent connection open.



\## Setup

1\. Install dependencies: `pip install -r requirements.txt`

2\. Generate dev certificates: `python scripts/gen\_certs.py` (creates `certs/` folder).



\## Running

\### The Coordinator

Runs the security, runtime, and network server.

```cmd

python scripts/coordinator\_main.py

